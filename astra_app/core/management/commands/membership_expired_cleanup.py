from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.email_context import user_email_context_from_user
from core.membership_notifications import send_membership_notification
from core.models import Membership
from core.views_utils import _first

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Remove expired memberships: drop FreeIPA group membership, delete Membership rows, "
        "and send expired emails via django-post-office."
    )

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if an email was already queued today.",
        )

    @override
    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))

        now = timezone.now()

        expired_memberships: Iterable[Membership] = (
            Membership.objects.select_related("membership_type")
            .filter(expires_at__isnull=False, expires_at__lte=now)
            .order_by("target_username", "membership_type_id")
        )

        removed = 0
        emailed = 0
        skipped = 0
        failed = 0

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

            if membership.membership_type.group_cn:
                try:
                    fu.remove_from_group(group_name=membership.membership_type.group_cn)
                except Exception:
                    failed += 1
                    logger.exception(
                        "membership_expired_cleanup_failure user=%s membership_type=%s group_cn=%s reason=freeipa_remove_failed",
                        membership.target_username,
                        membership.membership_type_id,
                        membership.membership_type.group_cn,
                    )
                    continue

            if fu.email:
                tz_name = str(_first(fu._user_data, "fasTimezone", "") or "").strip() or "UTC"
                did_queue = send_membership_notification(
                    recipient_email=fu.email,
                    username=membership.target_username,
                    membership_type=membership.membership_type,
                    template_name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                    expires_at=membership.expires_at,
                    force=force,
                    tz_name=tz_name,
                    user_context=user_email_context_from_user(user=fu),
                )
                if did_queue:
                    emailed += 1
                else:
                    skipped += 1

            membership.delete()
            removed += 1

        self.stdout.write(
            f"Removed {removed} membership(s); queued {emailed} email(s); skipped {skipped}; failed {failed}."
        )
