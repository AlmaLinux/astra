import datetime
import logging
import math
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import signals as astra_signals
from core.email_context import user_email_context_from_user
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.membership import get_expiring_memberships, memberships_with_open_request
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
    help = "Send membership expiration warning emails via django-post-office."

    @override
    def add_arguments(self, parser) -> None:
        add_force_and_dry_run_arguments(parser)

    @override
    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))
        dry_run: bool = bool(options.get("dry_run"))

        logger.info(
            "membership_expiration_notifications: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        today_utc = timezone.now().astimezone(datetime.UTC).date()

        NUMBER_OF_SCHEDULED_NOTIFICATIONS = 7
        schedule_days = [
            math.floor(settings.MEMBERSHIP_EXPIRING_SOON_DAYS / 2**i) for i in range(NUMBER_OF_SCHEDULED_NOTIFICATIONS)
        ]

        # The schedule uses day differences, so include the full cutoff date window.
        expiring_memberships = get_expiring_memberships(days=max(schedule_days) + 1)
        with_open_request = memberships_with_open_request(expiring_memberships)

        queued = 0
        skipped = 0
        due_memberships: list[tuple[Membership, int]] = []
        for membership in expiring_memberships:
            days_until = (membership.expires_at.astimezone(datetime.UTC).date() - today_utc).days
            if days_until not in schedule_days:
                continue
            if membership.pk not in with_open_request:
                due_memberships.append((membership, days_until))
                continue

            target = membership.target_username or f"org={membership.target_organization_id}"
            category_id = membership.membership_type.category_id
            if days_until == 1:
                if dry_run:
                    logger.info(
                        "[dry-run] Would extend expiration for %s/%s by 1 day due to open membership request.",
                        target,
                        category_id,
                    )
                else:
                    membership.expires_at = membership.expires_at + datetime.timedelta(days=1)
                    membership.save(update_fields=["expires_at"])
                    logger.info(
                        "Extended expiration for %s/%s by 1 day due to open membership request.",
                        target,
                        category_id,
                    )
            logger.info("Skipped expiring-soon email for %s; open membership request exists for %s.", target, category_id)

        for membership, days_until in due_memberships:
            if membership.target_organization is not None:
                continue

            template = settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME
            fu = FreeIPAUser.get(membership.target_username, respect_privacy=False)
            if fu is None or not fu.email:
                continue

            tz_name = _get_freeipa_timezone_name(fu)
            if dry_run:
                would_queue = would_queue_membership_notification(
                    force=force,
                    template_name=template,
                    recipient_email=fu.email,
                    membership_type=membership.membership_type,
                )

                if would_queue:
                    logger.info("[dry-run] Would queue %s to %s.", template, fu.email)
                    queued += 1
                else:
                    logger.info("[dry-run] Would skip %s to %s; already queued today.", template, fu.email)
                    skipped += 1
            else:
                did_queue = send_membership_notification(
                    recipient_email=fu.email,
                    membership_type=membership.membership_type,
                    template_name=template,
                    expires_at=membership.expires_at,
                    username=membership.target_username,
                    days=days_until,
                    force=force,
                    tz_name=tz_name,
                    extra_context=user_email_context_from_user(user=fu),
                )
                if did_queue:
                    queued += 1
                    logger.info("Queued %s to %s.", template, fu.email)
                else:
                    skipped += 1
                    logger.info("Skipped %s to %s; already queued today.", template, fu.email)

        for sponsorship, days_until in due_memberships:
            target_organization = sponsorship.target_organization
            if target_organization is None:
                continue

            template = settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME
            organization_id = target_organization.pk
            recipient_email, recipient_warning = organization_sponsor_notification_recipient_email(
                organization=target_organization,
                notification_kind="organization sponsorship expiring-soon",
            )
            if recipient_warning:
                logger.warning("%s", recipient_warning)
            if not recipient_email:
                # Nobody was told, so the committee has to chase this one itself.
                if not dry_run:
                    astra_signals.organization_membership_expiring_soon.send(
                        sender=astra_signals.MembershipExpirationCommand,
                        membership=sponsorship,
                        days=days_until,
                        sponsor_notified=False,
                    )
                continue

            extend_url = organization_membership_request_url(
                organization_id=organization_id,
                membership_type_code=sponsorship.membership_type.code,
                base_url=settings.PUBLIC_BASE_URL,
            )

            if dry_run:
                would_queue = would_queue_membership_notification(
                    force=force,
                    template_name=template,
                    recipient_email=recipient_email,
                    membership_type=sponsorship.membership_type,
                    organization=target_organization,
                )

                if would_queue:
                    logger.info("[dry-run] Would queue %s to %s.", template, recipient_email)
                    queued += 1
                else:
                    logger.info(
                        "[dry-run] Would skip %s to %s; already queued today.",
                        template,
                        recipient_email,
                    )
                    skipped += 1
            else:
                rep_user = (
                    FreeIPAUser.get(target_organization.representative, respect_privacy=False)
                    if target_organization.representative
                    else None
                )
                did_queue = send_membership_notification(
                    recipient_email=recipient_email,
                    membership_type=sponsorship.membership_type,
                    template_name=template,
                    expires_at=sponsorship.expires_at,
                    organization=target_organization,
                    membership=sponsorship,
                    days=days_until,
                    force=force,
                    tz_name=None if rep_user is None else _get_freeipa_timezone_name(rep_user),
                    extra_context={
                        "extend_url": extend_url,
                    },
                )
                if did_queue:
                    queued += 1
                    logger.info("Queued %s to %s.", template, recipient_email)
                    # Gated on the email so a same-day rerun doesn't post twice.
                    astra_signals.organization_membership_expiring_soon.send(
                        sender=astra_signals.MembershipExpirationCommand,
                        membership=sponsorship,
                        days=days_until,
                        sponsor_notified=True,
                    )
                else:
                    skipped += 1
                    logger.info("Skipped %s to %s; already queued today.", template, recipient_email)

        summary = f"Queued {queued} email(s); skipped {skipped}."
        if dry_run:
            logger.info("[dry-run] %s", summary)
        else:
            logger.info(summary)
            if queued > 0:
                astra_signals.membership_expiring_soon.send(
                    sender=astra_signals.MembershipExpirationCommand,
                    count=queued,
                    membership_type="all",
                )
