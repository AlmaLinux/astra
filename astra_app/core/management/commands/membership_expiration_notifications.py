from __future__ import annotations

import datetime
import math
from collections.abc import Iterable
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.email_context import organization_sponsor_email_context, user_email_context_from_user
from core.membership_notifications import (
    send_membership_notification,
    send_organization_sponsorship_notification,
)
from core.models import Membership, OrganizationSponsorship
from core.views_utils import _first


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

        memberships: Iterable[Membership] = (
            Membership.objects.select_related("membership_type")
            .order_by("target_username", "membership_type_id")
        )
        sponsorships = OrganizationSponsorship.objects.select_related(
            "organization",
            "membership_type",
        ).order_by("organization_id", "membership_type_id")

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
                would_queue = True
                if not force:
                    from post_office.models import Email

                    today = timezone.localdate()
                    already_sent = Email.objects.filter(
                        to=fu.email,
                        template__name=template,
                        context__membership_type_code=membership.membership_type.code,
                        created__date=today,
                    ).exists()
                    if already_sent:
                        would_queue = False

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
                    username=membership.target_username,
                    membership_type=membership.membership_type,
                    template_name=template,
                    expires_at=membership.expires_at,
                    days=days_until,
                    force=force,
                    tz_name=tz_name,
                    user_context=user_email_context_from_user(user=fu),
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

            sponsor_context = organization_sponsor_email_context(
                organization=sponsorship.organization,
            )
            recipient_email = str(sponsor_context.get("email") or "").strip()
            if not recipient_email:
                continue

            base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
            extend_path = reverse(
                "organization-sponsorship-extend",
                kwargs={"organization_id": sponsorship.organization_id},
            )
            extend_url = f"{base}{extend_path}" if base else extend_path

            if dry_run:
                would_queue = True
                if not force:
                    from post_office.models import Email

                    today = timezone.localdate()
                    already_sent = Email.objects.filter(
                        to=recipient_email,
                        template__name=template,
                        context__organization_id=sponsorship.organization_id,
                        context__membership_type_code=sponsorship.membership_type.code,
                        created__date=today,
                    ).exists()
                    if already_sent:
                        would_queue = False

                if would_queue:
                    self.stdout.write(f"[dry-run] Would queue {template} to {recipient_email}.")
                    queued += 1
                else:
                    self.stdout.write(
                        f"[dry-run] Would skip {template} to {recipient_email}; already queued today."
                    )
                    skipped += 1
            else:
                did_queue = send_organization_sponsorship_notification(
                    recipient_email=recipient_email,
                    organization=sponsorship.organization,
                    membership_type=sponsorship.membership_type,
                    template_name=template,
                    expires_at=sponsorship.expires_at,
                    days=days_until,
                    force=force,
                    extend_url=extend_url,
                    sponsor_context=sponsor_context,
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
