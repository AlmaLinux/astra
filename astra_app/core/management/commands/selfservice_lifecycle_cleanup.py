from typing import override

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.models import AccountDeletionRequest, MembershipTerminationFeedback


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

        feedback_qs = MembershipTerminationFeedback.objects.filter(
            ~Q(reason_text=""),
            reason_cleanup_due_at__isnull=False,
            reason_cleanup_due_at__lte=now,
            reason_text_cleared_at__isnull=True,
        )
        deletion_qs = AccountDeletionRequest.objects.filter(
            ~Q(reason_text=""),
            reason_cleanup_due_at__isnull=False,
            reason_cleanup_due_at__lte=now,
            reason_text_cleared_at__isnull=True,
        )

        feedback_count = feedback_qs.count()
        deletion_count = deletion_qs.count()

        if not dry_run:
            feedback_qs.update(reason_text="", reason_text_cleared_at=now)
            deletion_qs.update(reason_text="", reason_text_cleared_at=now)

        mode = "Would clear" if dry_run else "Cleared"
        self.stdout.write(
            f"{mode} {feedback_count} membership termination reason(s) and {deletion_count} account deletion reason(s)."
        )
