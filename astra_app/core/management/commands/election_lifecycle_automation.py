import datetime
import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.elections_services import close_election, election_quorum_status, start_scheduled_election
from core.models import AuditLogEntry, Election, ElectionAutoEndDeferral

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run due opt-in election lifecycle transitions."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    @override
    def handle(self, *args, **options) -> None:
        if not settings.ELECTION_LIFECYCLE_AUTOMATION_ENABLED:
            logger.info("election_lifecycle_automation: disabled")
            return

        now = timezone.now()
        dry_run = bool(options["dry_run"])
        verbosity = int(options["verbosity"])
        if bool(options["force"]):
            logger.info("election_lifecycle_automation: force requested; lifecycle policy is unchanged")
            self.stdout.write("--force does not override election lifecycle policy.")
        due_starts = list(Election.objects.active().filter(
            status=Election.Status.draft,
            auto_start_enabled=True,
            start_datetime__lte=now,
        ).values("id", "name", "start_datetime"))
        due_ends = list(Election.objects.active().filter(
            status=Election.Status.open,
            auto_end_enabled=True,
            end_datetime__lte=now,
        ).values("id", "name", "end_datetime"))
        self.stdout.write(f"Due automatic starts: {len(due_starts)}")
        self.stdout.write(f"Due automatic ends: {len(due_ends)}")
        for due_start in due_starts:
            election_id = int(due_start["id"])
            name = str(due_start["name"])
            scheduled_for = due_start["start_datetime"]
            try:
                if dry_run:
                    logger.info("election_lifecycle_automation: election_id=%s action=start evaluated_at=%s", election_id, now.isoformat())
                    if verbosity >= 2:
                        self.stdout.write(
                            f"Election {election_id} ({name}): scheduled start: {scheduled_for.isoformat()}; would start."
                        )
                else:
                    result = start_scheduled_election(election_id=election_id)
                    if verbosity >= 2:
                        reason = str(result.get("reason") or "").strip()
                        suffix = f" ({reason})" if reason else ""
                        self.stdout.write(
                            f"Election {election_id} ({name}): scheduled start: {scheduled_for.isoformat()}; "
                            f"start {result['status']}{suffix}."
                        )
            except Exception:
                logger.exception("election_lifecycle_automation: start failed election_id=%s", election_id)
                self.stderr.write(
                    f"Election {election_id} ({name}): scheduled start: {scheduled_for.isoformat()}; "
                    "automatic start failed; see logs."
                )
        for due_end in due_ends:
            election_id = int(due_end["id"])
            name = str(due_end["name"])
            scheduled_for = due_end["end_datetime"]
            try:
                outcome = self._process_due_end(election_id=election_id, now=now, dry_run=dry_run)
                if verbosity >= 2:
                    self.stdout.write(
                        f"Election {election_id} ({name}): scheduled end: {scheduled_for.isoformat()}; {outcome}."
                    )
            except Exception:
                logger.exception("election_lifecycle_automation: end failed election_id=%s", election_id)
                self.stderr.write(
                    f"Election {election_id} ({name}): scheduled end: {scheduled_for.isoformat()}; "
                    "automatic end failed; see logs."
                )

        if not due_starts and not due_ends:
            self.stdout.write(f"No election lifecycle actions are due as of {now.isoformat()}.")

    def _process_due_end(self, *, election_id: int, now: datetime.datetime, dry_run: bool) -> str:
        election = Election.objects.filter(pk=election_id).first()
        if election is None:
            return "skipped because it no longer exists"
        quorum = election_quorum_status(election=election)
        can_close = not bool(quorum["quorum_required"]) or bool(quorum["quorum_met"])
        if dry_run:
            action = "close" if can_close else "defer_quorum"
            logger.info("election_lifecycle_automation: election_id=%s action=%s evaluated_at=%s", election_id, action, now.isoformat())
            return f"would {action.replace('_', ' ')}"
        with transaction.atomic():
            election = Election.objects.select_for_update().get(pk=election_id)
            if election.status != Election.Status.open or not election.auto_end_enabled or election.end_datetime > now:
                return "skipped because it is no longer due"
            quorum = election_quorum_status(election=election)
            if not bool(quorum["quorum_required"]) or bool(quorum["quorum_met"]):
                election.auto_end_enabled = False
                election.save(update_fields=["auto_end_enabled", "updated_at"])
                close_election(
                    election=election,
                    actor="operations_hourly",
                    scheduled_for=election.end_datetime,
                )
                return "closed automatically"
            try:
                with transaction.atomic():
                    ElectionAutoEndDeferral.objects.create(election=election, scheduled_for=election.end_datetime)
            except IntegrityError:
                return "already deferred for unmet quorum"
            AuditLogEntry.objects.create(
                election=election,
                event_type="election_auto_end_deferred_quorum",
                payload={"scheduled_for": election.end_datetime.isoformat(), **quorum},
                is_public=False,
            )
            return "deferred for unmet quorum"