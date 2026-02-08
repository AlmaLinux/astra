from typing import override

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.elections_services import ElectionError, close_election, tally_election
from core.models import Election


class Command(BaseCommand):
    help = "Close and tally elections whose end_datetime has passed."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without modifying elections.",
        )

    @override
    def handle(self, *args, **options) -> None:
        dry_run: bool = bool(options.get("dry_run"))

        now = timezone.now()

        to_close = list(
            Election.objects.filter(status=Election.Status.open, end_datetime__lte=now).only("id")
        )
        to_tally = list(
            Election.objects.filter(status=Election.Status.closed, end_datetime__lte=now).only("id")
        )

        closed = 0
        tallied = 0
        failed = 0

        if dry_run:
            self.stdout.write(
                f"[dry-run] Would close {len(to_close)} election(s) and tally {len(to_tally)} election(s)."
            )
            return

        for election in to_close:
            try:
                close_election(election=election)
                closed += 1
            except ElectionError as exc:
                failed += 1
                self.stderr.write(f"Failed to close election {election.id}: {exc}")

        # Refresh the reference time: close_election() sets end_datetime=timezone.now(),
        # which can be slightly after the 'now' captured at command start.
        now = timezone.now()

        # Requery after close step so elections closed above are eligible to tally.
        to_tally = list(
            Election.objects.filter(status=Election.Status.closed, end_datetime__lte=now).only("id")
        )

        for election in to_tally:
            try:
                tally_election(election=election)
                tallied += 1
            except ElectionError as exc:
                failed += 1
                self.stderr.write(f"Failed to tally election {election.id}: {exc}")

        self.stdout.write(f"Closed {closed} election(s); tallied {tallied} election(s); failed {failed}.")
