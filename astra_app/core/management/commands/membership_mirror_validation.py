import logging
from typing import override

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.membership_constants import MembershipCategoryCode
from core.mirror_membership_validation import (
    _CLOSED_MEMBERSHIP_REQUEST_STATUSES,
    build_validation_debug_lines,
    claim_next_validation,
    claim_validation_for_request,
    dry_run_validations,
    finalize_validation,
    is_mirror_membership_request,
    run_validation,
    schedule_mirror_membership_validation,
)
from core.models import MembershipRequest, MirrorMembershipValidation

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
        parser.add_argument(
            "--fix",
            action="store_true",
            help=(
                "Ensure mirror validation rows exist for open mirror requests before processing due validations "
                "in the same run (may perform outbound HTTP)."
            ),
        )
        parser.add_argument(
            "--request-id",
            type=int,
            help="Run validation directly for one membership request ID for debugging.",
        )

    @override
    def handle(self, *args, **options) -> None:
        now = timezone.now()
        force: bool = bool(options.get("force"))
        dry_run: bool = bool(options.get("dry_run"))
        fix: bool = bool(options.get("fix"))
        request_id = options.get("request_id")

        if dry_run and fix:
            raise CommandError("--fix cannot be used with --dry-run")

        if request_id is not None:
            self._handle_direct_request(request_id=int(request_id), dry_run=dry_run)
            return

        if fix:
            self._handle_fix_missing_rows()

        if dry_run:
            validations = dry_run_validations(now=now, force=force)
            missing_request_ids = self._missing_open_mirror_request_ids()
            if not validations:
                self.stdout.write("dry-run: no mirror validation rows are due.")
                for request_id in missing_request_ids:
                    self.stdout.write(f"dry-run: missing mirror validation row for request {request_id}")
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
            for request_id in missing_request_ids:
                self.stdout.write(f"dry-run: missing mirror validation row for request {request_id}")
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
            self._write_debug_output(result=validation.result)
            if note_content is not None:
                self.stdout.write(f"wrote note for request {request_id}")

        if processed == 0:
            self.stdout.write("No mirror validation rows were due.")
            for request_id in self._missing_open_mirror_request_ids():
                self.stdout.write(f"missing mirror validation row for request {request_id}")

    def _missing_open_mirror_request_ids(self) -> list[int]:
        queryset = (
            MembershipRequest.objects.filter(
                membership_type__category_id=MembershipCategoryCode.mirror,
                mirror_validation__isnull=True,
            )
            .exclude(status__in=_CLOSED_MEMBERSHIP_REQUEST_STATUSES)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        return list(queryset)

    def _handle_fix_missing_rows(self) -> None:
        missing_requests = (
            MembershipRequest.objects.select_related("membership_type")
            .filter(
                membership_type__category_id=MembershipCategoryCode.mirror,
                mirror_validation__isnull=True,
            )
            .exclude(status__in=_CLOSED_MEMBERSHIP_REQUEST_STATUSES)
            .order_by("pk")
        )
        for membership_request in missing_requests:
            validation = schedule_mirror_membership_validation(membership_request=membership_request)
            if validation is None:
                continue
            self.stdout.write(f"ensured mirror validation row exists for request {membership_request.pk}")

    def _handle_direct_request(self, *, request_id: int, dry_run: bool) -> None:
        membership_request = MembershipRequest.objects.select_related("membership_type").filter(pk=request_id).first()
        if membership_request is None:
            raise CommandError(f"membership request ID {request_id} does not exist")
        if not is_mirror_membership_request(membership_request):
            raise CommandError(f"membership request ID {request_id} is not a mirror membership request")

        closed_validation_queryset = MirrorMembershipValidation.objects.filter(membership_request=membership_request)
        if membership_request.status in _CLOSED_MEMBERSHIP_REQUEST_STATUSES:
            if dry_run:
                if closed_validation_queryset.exists():
                    self.stdout.write(
                        f"dry-run: would delete closed-request validation for request ID {request_id} via --request-id",
                    )
                else:
                    self.stdout.write(
                        f"request ID {request_id} is closed; no validation row to delete via --request-id",
                    )
                return

            deleted_count, _deleted_detail = closed_validation_queryset.delete()
            deleted_validation = deleted_count > 0
            logger.info(
                "mirror_validation.deleted_closed_request_direct",
                extra={
                    "request_id": request_id,
                    "deleted_validation": deleted_validation,
                },
            )
            if deleted_validation:
                self.stdout.write(
                    f"deleted closed-request validation for request ID {request_id} via --request-id",
                )
            else:
                self.stdout.write(
                    f"request ID {request_id} is closed; no validation row to delete via --request-id",
                )
            return

        if dry_run:
            self.stdout.write(f"dry-run: would validate request ID {request_id} via --request-id")
            return

        now = timezone.now()
        validation = claim_validation_for_request(membership_request=membership_request, now=now)
        self.stdout.write(f"processing request ID {request_id} via --request-id")
        outcome = run_validation(membership_request=membership_request)
        note_content = finalize_validation(
            validation=validation,
            outcome=outcome,
            now=timezone.now(),
        )
        validation.refresh_from_db()

        logger.info(
            "mirror_validation.processed_direct",
            extra={
                "request_id": request_id,
                "validation_status": validation.status,
                "attempt_count": validation.attempt_count,
            },
        )
        self.stdout.write(f"processed request ID {request_id} status={validation.status}")
        self._write_debug_output(result=validation.result)
        if note_content is not None:
            self.stdout.write(f"wrote note for request ID {request_id}")

    def _write_debug_output(self, *, result: dict[str, object]) -> None:
        for line in build_validation_debug_lines(result=result):
            self.stdout.write(line)
