import logging
from typing import override

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.mirror_membership_validation import (
    _CLOSED_MEMBERSHIP_REQUEST_STATUSES,
    claim_next_validation,
    dry_run_validations,
    finalize_validation,
    run_validation,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Validate scheduled mirror membership requests outside the request/redirect path."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Process eligible non-terminal rows even when backoff has not elapsed.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report due rows without mutating validation state or writing notes.",
        )

    @override
    def handle(self, *args, **options) -> None:
        now = timezone.now()
        force: bool = bool(options.get("force"))
        dry_run: bool = bool(options.get("dry_run"))

        if dry_run:
            validations = dry_run_validations(now=now, force=force)
            if not validations:
                self.stdout.write("dry-run: no mirror validation rows are due.")
                return
            for validation in validations:
                request_id = validation.membership_request.pk
                if validation.membership_request.status in _CLOSED_MEMBERSHIP_REQUEST_STATUSES:
                    self.stdout.write(
                        f"dry-run: would delete closed-request validation for request {request_id}",
                    )
                    continue
                self.stdout.write(
                    f"dry-run: would validate request {request_id} status={validation.status}",
                )
            return

        processed = 0
        while True:
            claimed = claim_next_validation(now=timezone.now(), force=force)
            if claimed is None:
                break

            validation, reclaimed = claimed
            request_id = validation.membership_request.pk
            if reclaimed:
                self.stdout.write(f"reclaimed expired claim for request {request_id}")

            if validation.membership_request.status in _CLOSED_MEMBERSHIP_REQUEST_STATUSES:
                validation.delete()
                processed += 1
                logger.info(
                    "mirror_validation.deleted_closed_request",
                    extra={
                        "request_id": request_id,
                        "reclaimed": reclaimed,
                    },
                )
                self.stdout.write(f"deleted closed-request validation for request {request_id}")
                continue

            outcome = run_validation(membership_request=validation.membership_request)
            note_content = finalize_validation(
                validation=validation,
                outcome=outcome,
                now=timezone.now(),
            )
            validation.refresh_from_db()
            processed += 1

            logger.info(
                "mirror_validation.processed",
                extra={
                    "request_id": request_id,
                    "validation_status": validation.status,
                    "attempt_count": validation.attempt_count,
                    "reclaimed": reclaimed,
                },
            )
            self.stdout.write(
                f"processed request {request_id} status={validation.status}",
            )
            if note_content is not None:
                self.stdout.write(f"wrote note for request {request_id}")

        if processed == 0:
            self.stdout.write("No mirror validation rows were due.")
