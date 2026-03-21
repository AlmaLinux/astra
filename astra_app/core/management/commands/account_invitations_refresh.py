import logging
from typing import Any, override

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.account_invitations import refresh_account_invitations

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Refresh account invitation status from FreeIPA."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--actor",
            dest="actor",
            default="system",
            help="Username recorded as the refresh actor (default: system).",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        actor = str(options.get("actor") or "").strip() or "system"
        summary = refresh_account_invitations(actor_username=actor, now=timezone.now())
        total_checked = summary.pending_checked + summary.accepted_checked
        total_updated = summary.pending_updated + summary.accepted_updated
        logger.info(
            "Checked %s invitations (pending=%s, accepted=%s). Updated %s invitations.",
            total_checked,
            summary.pending_checked,
            summary.accepted_checked,
            total_updated,
        )
