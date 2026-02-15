import logging
from collections.abc import Iterable
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.email_context import (
    organization_email_context_from_organization,
    organization_sponsor_email_context,
    user_email_context_from_user,
)
from core.ipa_user_attrs import _first
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    remove_organization_representative_from_group_if_present,
    remove_user_from_group,
)
from core.membership_notifications import (
    organization_membership_request_url,
    organization_sponsor_notification_recipient_email,
    send_membership_notification,
    would_queue_membership_notification,
)
from core.models import Membership

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Remove expired memberships: drop FreeIPA group membership, delete "
        "Membership rows, and send "
        "expired emails via django-post-office."
    )

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if an email was already queued today.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without mutating data or sending email.",
        )

    @override
    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))
        dry_run: bool = bool(options.get("dry_run"))

        now = timezone.now()

        expired_memberships: Iterable[Membership] = (
            Membership.objects.select_related("membership_type")
            .filter(
                target_organization__isnull=True,
                expires_at__isnull=False,
                expires_at__lte=now,
            )
            .exclude(target_username="")
            .order_by("target_username", "membership_type_id")
        )
        expired_sponsorships: Iterable[Membership] = (
            Membership.objects.select_related("target_organization", "membership_type")
            .filter(
                target_organization__isnull=False,
                expires_at__isnull=False,
                expires_at__lte=now,
            )
            .order_by("target_organization_id", "membership_type_id")
        )

        removed = 0
        emailed = 0
        skipped = 0
        failed = 0
        sponsorship_removed = 0
        sponsorship_emailed = 0
        sponsorship_skipped = 0
        sponsorship_failed = 0

        for membership in expired_memberships:
            fu = FreeIPAUser.get(membership.target_username)
            self.stdout.write(f"Processing expired membership for user {membership.target_username}...")
            if fu is None:
                failed += 1
                logger.warning(
                    "membership_expired_cleanup_failure user=%s membership_type=%s group_cn=%s reason=freeipa_user_missing",
                    membership.target_username,
                    membership.membership_type_id,
                    membership.membership_type.group_cn,
                )
                continue

            group_cn = str(membership.membership_type.group_cn or "").strip()
            if group_cn:
                if dry_run:
                    self.stdout.write(
                        "[dry-run] Would remove user "
                        f"{membership.target_username} from group {group_cn}."
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

            if fu.email:
                tz_name = str(_first(fu._user_data, "fasTimezone", "") or "").strip() or "UTC"
                if dry_run:
                    would_queue = would_queue_membership_notification(
                        force=force,
                        template_name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        recipient_email=fu.email,
                        membership_type=membership.membership_type,
                    )

                    if would_queue:
                        self.stdout.write(
                            "[dry-run] Would queue "
                            f"{settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME} to {fu.email}."
                        )
                        emailed += 1
                    else:
                        self.stdout.write(
                            "[dry-run] Would skip email for "
                            f"{fu.email}; already queued today."
                        )
                        skipped += 1
                else:
                    did_queue = send_membership_notification(
                        recipient_email=fu.email,
                        membership_type=membership.membership_type,
                        template_name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        expires_at=membership.expires_at,
                        username=membership.target_username,
                        force=force,
                        tz_name=tz_name,
                        extra_context=user_email_context_from_user(user=fu),
                    )
                    if did_queue:
                        emailed += 1
                    else:
                        skipped += 1

            if dry_run:
                self.stdout.write(
                    "[dry-run] Would delete membership "
                    f"{membership.target_username}:{membership.membership_type_id}."
                )
                removed += 1
            else:
                membership.delete()
                removed += 1

        for sponsorship in expired_sponsorships:
            org = sponsorship.target_organization
            if org is None:
                continue
            membership_type = sponsorship.membership_type
            group_cn = str(membership_type.group_cn or "").strip()
            rep_username = str(org.representative or "").strip()
            removal_failed = False

            self.stdout.write(
                "Processing expired sponsorship for org "
                f"{org.pk} (level={membership_type.code}) rep={rep_username!r}..."
            )

            rep = None
            if group_cn and rep_username:
                rep = FreeIPAUser.get(rep_username)
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
                        self.stdout.write(
                            "[dry-run] Would remove representative "
                            f"{rep_username} from group {group_cn}."
                        )
                    else:
                        outcome = remove_organization_representative_from_group_if_present(
                            representative_username=rep_username,
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

            if removal_failed:
                self.stdout.write(
                    f"Skipping expired sponsorship cleanup for org {org.pk}; FreeIPA removal failed."
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
                self.stderr.write(f"Warning: {recipient_warning}")
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
                        self.stdout.write(
                            "[dry-run] Would queue "
                            f"{settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME} to {recipient_email}."
                        )
                        sponsorship_emailed += 1
                    else:
                        self.stdout.write(
                            "[dry-run] Would skip email for "
                            f"{recipient_email}; already queued today."
                        )
                        sponsorship_skipped += 1
                else:
                    tz_name = "UTC"
                    if rep is not None:
                        tz_name = str(_first(rep._user_data, "fasTimezone", "") or "").strip() or "UTC"
                    did_queue = send_membership_notification(
                        recipient_email=recipient_email,
                        membership_type=membership_type,
                        template_name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                        expires_at=sponsorship.expires_at,
                        organization=org,
                        force=force,
                        tz_name=tz_name,
                        extra_context=sponsor_context | {"extend_url": request_url},
                    )
                    if did_queue:
                        sponsorship_emailed += 1
                    else:
                        sponsorship_skipped += 1

            if dry_run:
                self.stdout.write(
                    "[dry-run] Would delete sponsorship "
                    f"org={org.pk} membership_type={membership_type.code}."
                )
                sponsorship_removed += 1
            else:
                sponsorship.delete()
                sponsorship_removed += 1

        membership_summary = (
            f"Removed {removed} membership(s); queued {emailed} email(s); skipped {skipped}; failed {failed}."
        )
        sponsorship_summary = (
            "Removed "
            f"{sponsorship_removed} sponsorship(s); queued {sponsorship_emailed} email(s); "
            f"skipped {sponsorship_skipped}; failed {sponsorship_failed}."
        )
        if dry_run:
            self.stdout.write(f"[dry-run] {membership_summary}")
            self.stdout.write(f"[dry-run] {sponsorship_summary}")
        else:
            self.stdout.write(membership_summary)
            self.stdout.write(sponsorship_summary)
