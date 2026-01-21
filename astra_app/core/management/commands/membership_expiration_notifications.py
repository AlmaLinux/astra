from __future__ import annotations

import datetime
import math
from collections.abc import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.email_context import user_email_context_from_user
from core.membership_notifications import send_membership_notification
from core.models import Membership
from core.views_utils import _first


class Command(BaseCommand):
    help = "Send membership expiration warning emails via django-post-office."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if an email was already queued today.",
        )

    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))

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

        self.stdout.write(f"Queued {queued} email(s); skipped {skipped}.")
