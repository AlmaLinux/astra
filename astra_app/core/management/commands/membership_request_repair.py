import json
from typing import override

from django.core.management.base import BaseCommand, CommandError

from core.membership_request_repairs import reset_rejected_membership_request_to_pending
from core.models import MembershipRequest


class Command(BaseCommand):
    help = "Run targeted membership-request repairs for operator recovery workflows."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument("--request-id", type=int, required=True, help="Membership request ID to repair.")
        parser.add_argument(
            "--reset-to-pending",
            action="store_true",
            help="Reset a rejected request back to pending and clear decision metadata.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview the repair without mutating data.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the repair. Requires --actor and --reason.",
        )
        parser.add_argument("--actor", default="", help="Username to attribute the repair to.")
        parser.add_argument("--reason", default="", help="Operator note content explaining the repair.")

    @override
    def handle(self, *args, **options) -> None:
        request_id = int(options["request_id"])
        reset_to_pending = bool(options.get("reset_to_pending"))
        dry_run = bool(options.get("dry_run"))
        apply_changes = bool(options.get("apply"))
        actor_username = str(options.get("actor") or "").strip()
        note_content = str(options.get("reason") or "").strip()

        if not reset_to_pending:
            raise CommandError("Choose one repair action. Currently supported: --reset-to-pending.")
        if dry_run and apply_changes:
            raise CommandError("Choose only one of --dry-run or --apply.")
        if apply_changes and (not actor_username or not note_content):
            raise CommandError("--apply requires both --actor and --reason.")

        effective_apply = apply_changes

        try:
            membership_request = MembershipRequest.objects.get(pk=request_id)
        except MembershipRequest.DoesNotExist as exc:
            raise CommandError(f"membership request ID {request_id} does not exist") from exc

        try:
            result = reset_rejected_membership_request_to_pending(
                membership_request=membership_request,
                actor_username=actor_username,
                note_content=note_content,
                apply_changes=effective_apply,
            )
        except Exception as exc:
            from django.core.exceptions import ValidationError

            if isinstance(exc, ValidationError):
                raise CommandError(exc.message) from exc
            raise

        self.stdout.write(json.dumps(result.to_dict()))