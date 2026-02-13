import datetime
import math
from collections.abc import Iterable
from typing import override
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.email_context import user_email_context_from_user
from core.ipa_user_attrs import _first
from core.membership import get_expiring_memberships
from core.membership_notifications import (
    organization_membership_request_url,
    organization_sponsor_notification_recipient_email,
    send_membership_notification,
    would_queue_membership_notification,
)
from core.models import Membership


class Command(BaseCommand):
    help = "Send membership expiration warning emails via django-post-office."

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
        today_utc = now.astimezone(datetime.UTC).date()

        NUMBER_OF_SCHEDULED_NOTIFICATIONS = 7
        schedule_divisors = (2**i for i in range(NUMBER_OF_SCHEDULED_NOTIFICATIONS))
        schedule_days = [
            math.floor(settings.MEMBERSHIP_EXPIRING_SOON_DAYS / divisor)
            for divisor in schedule_divisors
        ]

        max_schedule_days = max(schedule_days) if schedule_days else 0
        window_days = max_schedule_days + 1
        # The schedule uses day differences, so include the full cutoff date window.
        expiring_memberships = get_expiring_memberships(days=window_days)
        memberships: Iterable[Membership] = [
            membership
            for membership in expiring_memberships
            if str(membership.target_username or "").strip()
        ]
        sponsorships: Iterable[Membership] = [
            membership
            for membership in expiring_memberships
            if membership.target_organization_id is not None
        ]

        queued = 0
        skipped = 0

        for membership in memberships:
            if not membership.expires_at:
                continue

            if membership.expires_at <= now:
                continue

            expires_on_utc = membership.expires_at.astimezone(datetime.UTC).date()
            days_until = (expires_on_utc - today_utc).days

            if days_until not in schedule_days:
                continue

            template = settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME

            fu = FreeIPAUser.get(membership.target_username)
            if fu is None or not fu.email:
                continue

            tz_name = str(_first(fu._user_data, "fasTimezone", "") or "").strip() or "UTC"
            if dry_run:
                would_queue = would_queue_membership_notification(
                    force=force,
                    template_name=template,
                    recipient_email=fu.email,
                    membership_type=membership.membership_type,
                )

                if would_queue:
                    self.stdout.write(f"[dry-run] Would queue {template} to {fu.email}.")
                    queued += 1
                else:
                    self.stdout.write(
                        f"[dry-run] Would skip {template} to {fu.email}; already queued today."
                    )
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
                else:
                    skipped += 1

        for sponsorship in sponsorships:
            if not sponsorship.expires_at:
                continue

            if sponsorship.expires_at <= now:
                continue

            expires_on_utc = sponsorship.expires_at.astimezone(datetime.UTC).date()
            days_until = (expires_on_utc - today_utc).days

            if days_until not in schedule_days:
                continue

            template = settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME

            target_organization = sponsorship.target_organization
            recipient_email, recipient_warning = organization_sponsor_notification_recipient_email(
                organization=target_organization,
                notification_kind="organization sponsorship expiring-soon",
            )
            if recipient_warning:
                self.stderr.write(f"Warning: {recipient_warning}")
            if not recipient_email:
                continue

            extend_url = organization_membership_request_url(
                organization_id=sponsorship.target_organization_id,
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
                    self.stdout.write(f"[dry-run] Would queue {template} to {recipient_email}.")
                    queued += 1
                else:
                    self.stdout.write(
                        f"[dry-run] Would skip {template} to {recipient_email}; already queued today."
                    )
                    skipped += 1
            else:
                rep_timezone: str = "UTC"
                representative_username = str(sponsorship.target_organization.representative or "").strip()
                if representative_username:
                    rep_user = FreeIPAUser.get(representative_username)
                    if rep_user is not None:
                        candidate_tz = str(_first(rep_user._user_data, "fasTimezone", "") or "").strip()
                        if candidate_tz:
                            try:
                                ZoneInfo(candidate_tz)
                            except Exception:
                                rep_timezone = "UTC"
                            else:
                                rep_timezone = candidate_tz

                did_queue = send_membership_notification(
                    recipient_email=recipient_email,
                    membership_type=sponsorship.membership_type,
                    template_name=template,
                    expires_at=sponsorship.expires_at,
                    organization=sponsorship.target_organization,
                    days=days_until,
                    force=force,
                    tz_name=rep_timezone,
                    extra_context={
                        "extend_url": extend_url,
                    },
                )
                if did_queue:
                    queued += 1
                else:
                    skipped += 1

        summary = f"Queued {queued} email(s); skipped {skipped}."
        if dry_run:
            self.stdout.write(f"[dry-run] {summary}")
        else:
            self.stdout.write(summary)
