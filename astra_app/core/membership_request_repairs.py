from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from core.membership_notes import add_note
from core.models import MembershipRequest


@dataclass(frozen=True, slots=True)
class MembershipRequestRepairResult:
    request_id: int
    from_status: str
    to_status: str
    dry_run: bool
    trimmed_rejection_reason: bool
    note_created: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "dry_run": self.dry_run,
            "trimmed_rejection_reason": self.trimmed_rejection_reason,
            "note_created": self.note_created,
        }


def _trim_trailing_rejection_reason(responses: Any) -> tuple[list[Any], bool]:
    normalized_responses = list(responses) if isinstance(responses, list) else []
    if not normalized_responses:
        return normalized_responses, False

    trailing_response = normalized_responses[-1]
    if not isinstance(trailing_response, dict):
        return normalized_responses, False
    if set(trailing_response.keys()) != {"Rejection reason"}:
        return normalized_responses, False

    return normalized_responses[:-1], True


def reset_rejected_membership_request_to_pending(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    note_content: str,
    apply_changes: bool,
) -> MembershipRequestRepairResult:
    if membership_request.pk is None:
        raise ValidationError("Membership request must be saved before repair")

    actor = str(actor_username).strip()
    note = str(note_content).strip()
    if apply_changes and (not actor or not note):
        raise ValidationError("Repairing a request requires both actor username and reason")

    if not apply_changes:
        current_request = MembershipRequest.objects.get(pk=membership_request.pk)
        if current_request.status != MembershipRequest.Status.rejected:
            raise ValidationError("Only rejected requests can be reset to pending")

        _responses, trimmed_rejection_reason = _trim_trailing_rejection_reason(current_request.responses)
        return MembershipRequestRepairResult(
            request_id=current_request.pk,
            from_status=current_request.status,
            to_status=MembershipRequest.Status.pending,
            dry_run=True,
            trimmed_rejection_reason=trimmed_rejection_reason,
            note_created=False,
        )

    with transaction.atomic():
        current_request = MembershipRequest.objects.select_for_update(of=("self",)).get(pk=membership_request.pk)
        if current_request.status != MembershipRequest.Status.rejected:
            raise ValidationError("Only rejected requests can be reset to pending")

        responses, trimmed_rejection_reason = _trim_trailing_rejection_reason(current_request.responses)
        current_request.responses = responses
        current_request.status = MembershipRequest.Status.pending
        current_request.on_hold_at = None
        current_request.decided_at = None
        current_request.decided_by_username = ""
        current_request.save(
            update_fields=[
                "responses",
                "status",
                "on_hold_at",
                "decided_at",
                "decided_by_username",
            ]
        )
        add_note(
            membership_request=current_request,
            username=actor,
            content=note,
        )

    return MembershipRequestRepairResult(
        request_id=current_request.pk,
        from_status=MembershipRequest.Status.rejected,
        to_status=current_request.status,
        dry_run=False,
        trimmed_rejection_reason=trimmed_rejection_reason,
        note_created=True,
    )