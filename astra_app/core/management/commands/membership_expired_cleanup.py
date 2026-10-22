import datetime
import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import signals as astra_signals
from core.email_context import (
    organization_email_context_from_organization,
    organization_sponsor_email_context,
    user_email_context_from_user,
)
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    memberships_with_open_request,
    remove_user_from_group,
    remove_user_from_group_if_present,
)
from core.membership_notifications import (
    organization_membership_request_url,
    organization_sponsor_notification_recipient_email,
    send_membership_notification,
    would_queue_membership_notification,
)
from core.models import Membership
from core.operations import add_force_and_dry_run_arguments

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Remove expired memberships: drop FreeIPA group membership, delete "
        "Membership rows, and send "
        "expired emails via django-post-office."
    )

    @override
    def add_arguments(self, parser) -> None:
        add_force_and_dry_run_arguments(parser)

    @override
    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))
        dry_run: bool = bool(options.get("dry_run"))

        now = timezone.now()

        logger.info(
            "membership_expired_cleanup: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        expired = Membership.objects.select_related("target_organization", "membership_type").filter(expires_at__lte=now)
        expired_membership_rows = list(
            expired.filter(target_organization__isnull=True).order_by("target_username", "membership_type_id")
        )
        expired_sponsorship_rows = list(
            expired.filter(target_organization__isnull=False).order_by("target_organization_id", "membership_type_id")
        )
        with_open_request = memberships_with_open_request([*expired_membership_rows, *expired_sponsorship_rows])
        # Memberships with an open request get a day's grace so the request can still be decided.
        preserved_expires_at = now + datetime.timedelta(days=1)

        removed = 0
        emailed = 0
        skipped = 0
        failed = 0
        sponsorship_removed = 0
        sponsorship_emailed = 0
        sponsorship_skipped = 0
        sponsorship_failed = 0

        for membership in expired_membership_rows:
            if membership.pk in with_open_request:
                logger.info(
                    "membership_expired_cleanup: skipped expired membership user=%s membership_category=%s due to open membership request; preserving through %s",
                    membership.target_username,
                    membership.membership_type.category_id,
                    preserved_expires_at,
                )
                if not dry_run:
                    membership.expires_at = preserved_expires_at
                    membership.save(update_fields=["expires_at"])
                continue

            fu = FreeIPAUser.get(membership.target_username, respect_privacy=False)
            logger.info(
                "membership_expired_cleanup: processing expired membership user=%s membership_type=%s",
                membership.target_username,
                membership.membership_type_id,
            )
            if fu is None:
                logger.debug(
                    "membership_expired_cleanup_freeipa_user_missing user=%s membership_type=%s group_cn=%s",
                    membership.target_username,
                    membership.membership_type_id,
                    membership.membership_type.group_cn,
                )
                logger.info(
                    "membership_expired_cleanup: FreeIPA user missing for %s membership_type=%s",
                    membership.target_username,
                    membership.membership_type_id,
                )
                if dry_run:
                    logger.info(
                        "[dry-run] Would delete membership %s:%s.",
                        membership.target_username,
                        membership.membership_type_id,
                    )
                else:
                    membership.delete()
                removed += 1
                continue

            group_cn = membership.membership_type.group_cn
            if group_cn:
                if dry_run:
                    logger.info(
                        "[dry-run] Would remove user %s from group %s.",
                        membership.target_username,
                        group_cn,
                    )
                else:
                    if not remove_user_from_group(
                        username=membership.target_username,
                        group_cn=group_cn,
                    ):
                        failed += 1
                        logger.error(
                            "membership_expired_cleanup_failure user=%s membership_type=%s group_cn=%s reason=freeipa_remove_failed",
                            membership.target_username,
                            membership.membership_type_id,
                            group_cn,
                        )
                        continue
                    logger.info(
                        "membership_expired_cleanup: removed user %s from group %s",
                        membership.target_username,
                        group_cn,
                    )

            if fu.email:
                if dry_run:
                    would_queue = would_queue_membership_notification(
                        force=force,
                        template_name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        recipient_email=fu.email,
                        membership_type=membership.membership_type,
                    )

                    if would_queue:
                        logger.info(
                            "[dry-run] Would queue %s to %s.",
                            settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                            fu.email,
                        )
                        emailed += 1
                    else:
                        logger.info("[dry-run] Would skip email for %s; already queued today.", fu.email)
                        skipped += 1
                else:
                    did_queue = send_membership_notification(
                        recipient_email=fu.email,
                        membership_type=membership.membership_type,
                        template_name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        expires_at=membership.expires_at,
                        username=membership.target_username,
                        force=force,
                        tz_name=_get_freeipa_timezone_name(fu),
                        extra_context=user_email_context_from_user(user=fu),
                    )
                    if did_queue:
                        emailed += 1
                        logger.info(
                            "membership_expired_cleanup: queued %s to %s",
                            settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                            fu.email,
                        )
                    else:
                        skipped += 1
                        logger.info("membership_expired_cleanup: skipped email for %s; already queued today.", fu.email)

            if dry_run:
                logger.info(
                    "[dry-run] Would delete membership %s:%s.",
                    membership.target_username,
                    membership.membership_type_id,
                )
                removed += 1
            else:
                membership.delete()
                removed += 1
                logger.info(
                    "membership_expired_cleanup: deleted membership %s:%s",
                    membership.target_username,
                    membership.membership_type_id,
                )

        for sponsorship in expired_sponsorship_rows:
            org = sponsorship.target_organization
            if org is None:
                continue
            membership_type = sponsorship.membership_type
            if sponsorship.pk in with_open_request:
                logger.info(
                    "membership_expired_cleanup: skipped expired sponsorship org_id=%s membership_category=%s due to open membership request; preserving through %s",
                    org.pk,
                    membership_type.category_id,
                    preserved_expires_at,
                )
                if not dry_run:
                    sponsorship.expires_at = preserved_expires_at
                    sponsorship.save(update_fields=["expires_at"])
                continue

            group_cn = membership_type.group_cn
            rep_username = org.representative
            removal_failed = False

            logger.info(
                "membership_expired_cleanup: processing expired sponsorship org_id=%s membership_type=%s representative=%s",
                org.pk,
                membership_type.code,
                rep_username,
            )

            rep = None
            if group_cn and rep_username:
                rep = FreeIPAUser.get(rep_username, respect_privacy=False)
                if rep is None:
                    sponsorship_failed += 1
                    removal_failed = True
                    logger.warning(
                        "organization_sponsorship_expired_cleanup_failure org_id=%s membership_type=%s group_cn=%s representative=%s reason=freeipa_user_missing",
                        org.pk,
                        membership_type.code,
                        group_cn,
                        rep_username,
                    )
                elif group_cn in rep.groups_list:
                    if dry_run:
                        logger.info(
                            "[dry-run] Would remove representative %s from group %s.",
                            rep_username,
                            group_cn,
                        )
                    else:
                        outcome = remove_user_from_group_if_present(
                            username=rep_username,
                            group_cn=group_cn,
                            caller_mode=FreeIPACallerMode.best_effort,
                            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                        )
                        if outcome == FreeIPAGroupRemovalOutcome.failed:
                            sponsorship_failed += 1
                            removal_failed = True
                            logger.error(
                                "organization_sponsorship_expired_cleanup_failure org_id=%s membership_type=%s group_cn=%s representative=%s reason=freeipa_remove_failed",
                                org.pk,
                                membership_type.code,
                                group_cn,
                                rep_username,
                            )
                        else:
                            logger.info(
                                "membership_expired_cleanup: removed representative %s from group %s",
                                rep_username,
                                group_cn,
                            )

            if removal_failed:
                logger.info(
                    "Skipping expired sponsorship cleanup for org %s; FreeIPA removal failed.",
                    org.pk,
                )
                # Posted on every retry: the sponsor keeps its access until someone fixes FreeIPA.
                if not dry_run:
                    astra_signals.organization_membership_expired.send(
                        sender=astra_signals.MembershipExpirationCommand,
                        membership=sponsorship,
                        sponsor_notified=False,
                        cleanup_failed=True,
                    )
                continue

            sponsor_context: dict[str, str]
            if rep is not None:
                sponsor_context = (
                    organization_email_context_from_organization(organization=org)
                    | user_email_context_from_user(user=rep)
                )
            else:
                sponsor_context = organization_sponsor_email_context(organization=org)

            recipient_email, recipient_warning = organization_sponsor_notification_recipient_email(
                organization=org,
                notification_kind="organization sponsorship expired-cleanup",
            )
            if recipient_warning:
                logger.warning("%s", recipient_warning)
            if recipient_email:
                request_url = organization_membership_request_url(
                    organization_id=org.pk,
                    membership_type_code=membership_type.code,
                    base_url=settings.PUBLIC_BASE_URL,
                )

                if dry_run:
                    would_queue = would_queue_membership_notification(
                        force=force,
                        template_name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        recipient_email=recipient_email,
                        membership_type=membership_type,
                        organization=org,
                    )

                    if would_queue:
                        logger.info(
                            "[dry-run] Would queue %s to %s.",
                            settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                            recipient_email,
                        )
                        sponsorship_emailed += 1
                    else:
                        logger.info("[dry-run] Would skip email for %s; already queued today.", recipient_email)
                        sponsorship_skipped += 1
                else:
                    did_queue = send_membership_notification(
                        recipient_email=recipient_email,
                        membership_type=membership_type,
                        template_name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        expires_at=sponsorship.expires_at,
                        organization=org,
                        membership=sponsorship,
                        force=force,
                        tz_name=None if rep is None else _get_freeipa_timezone_name(rep),
                        extra_context=sponsor_context | {"extend_url": request_url},
                    )
                    if did_queue:
                        sponsorship_emailed += 1
                        logger.info(
                            "membership_expired_cleanup: queued %s to %s",
                            settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                            recipient_email,
                        )
                    else:
                        sponsorship_skipped += 1
                        logger.info(
                            "membership_expired_cleanup: skipped email for %s; already queued today.",
                            recipient_email,
                        )

            if dry_run:
                logger.info(
                    "[dry-run] Would delete sponsorship org=%s membership_type=%s.",
                    org.pk,
                    membership_type.code,
                )
                sponsorship_removed += 1
            else:
                sponsorship.delete()
                sponsorship_removed += 1
                logger.info(
                    "membership_expired_cleanup: deleted sponsorship org=%s membership_type=%s",
                    org.pk,
                    membership_type.code,
                )
                astra_signals.organization_membership_expired.send(
                    sender=astra_signals.MembershipExpirationCommand,
                    membership=sponsorship,
                    sponsor_notified=bool(recipient_email),
                    cleanup_failed=False,
                )

        membership_summary = (
            f"Removed {removed} membership(s); queued {emailed} email(s); skipped {skipped}; failed {failed}."
        )
        sponsorship_summary = (
            "Removed "
            f"{sponsorship_removed} sponsorship(s); queued {sponsorship_emailed} email(s); "
            f"skipped {sponsorship_skipped}; failed {sponsorship_failed}."
        )
        if dry_run:
            logger.info("[dry-run] %s", membership_summary)
            logger.info("[dry-run] %s", sponsorship_summary)
        else:
            logger.info(membership_summary)
            logger.info(sponsorship_summary)
            if removed + sponsorship_removed > 0:
                astra_signals.membership_expired.send(
                    sender=astra_signals.MembershipExpirationCommand,
                    count=removed + sponsorship_removed,
                )
