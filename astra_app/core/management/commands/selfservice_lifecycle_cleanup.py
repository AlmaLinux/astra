import logging
from typing import override

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import AccountDeletionRequest, MembershipTerminationFeedback

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Clear expired free-text reason fields from self-service lifecycle records."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which rows would be cleared without mutating data.",
        )

    @override
    def handle(self, *args, **options) -> None:
        dry_run: bool = bool(options.get("dry_run"))
        now = timezone.now()

        feedback_qs = MembershipTerminationFeedback.objects.due_for_reason_cleanup(now=now)
        deletion_qs = AccountDeletionRequest.objects.due_for_reason_cleanup(now=now)

        feedback_count = feedback_qs.count()
        deletion_count = deletion_qs.count()

        logger.info(
            "selfservice_lifecycle_cleanup: %s %s membership termination reason(s) and %s account deletion reason(s).",
            "would clear" if dry_run else "clearing",
            feedback_count,
            deletion_count,
        )

        if not dry_run:
            feedback_qs.clear_reason_text(now=now)
            deletion_qs.clear_reason_text(now=now)

        logger.info(
            "selfservice_lifecycle_cleanup: %s %s membership termination reason(s) and %s account deletion reason(s).",
            "Would clear" if dry_run else "Cleared",
            feedback_count,
            deletion_count,
        )
