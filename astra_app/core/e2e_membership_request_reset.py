import datetime
from collections.abc import Sequence

from core.membership_notes import add_note
from core.membership_request_workflow import (
    approve_membership_request,
    ignore_membership_request,
    put_membership_request_on_hold,
    record_membership_request_created,
    reject_membership_request,
    resubmit_membership_request,
)
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Note, Organization

type WorkflowReviewNoteAction = str
type WorkflowReviewNote = tuple[str, WorkflowReviewNoteAction, str, datetime.datetime]
type WorkflowFinalState = str


def seed_membership_request_workflow(
    *,
    requested_username: str,
    requested_organization: Organization | None,
    membership_type: MembershipType,
    initial_responses: list[dict[str, str]],
    requested_at: datetime.datetime,
    review_notes: Sequence[WorkflowReviewNote],
    final_state: WorkflowFinalState,
    final_actor_username: str,
    final_action_at: datetime.datetime | None = None,
    rejection_reason: str = "",
    rfi_message: str = "",
    application_url: str,
    resubmitted_responses: list[dict[str, str]] | None = None,
    resubmitted_at: datetime.datetime | None = None,
    approved_expires_at: datetime.datetime | None = None,
) -> MembershipRequest:
    membership_request = MembershipRequest.objects.create(
        requested_username=requested_username,
        requested_organization=requested_organization,
        membership_type=membership_type,
        status=MembershipRequest.Status.pending,
        responses=initial_responses,
    )
    record_membership_request_created(
        membership_request=membership_request,
        actor_username=requested_username or requested_organization.representative,
        send_submitted_email=False,
    )
    MembershipRequest.objects.filter(pk=membership_request.pk).update(requested_at=requested_at)
    membership_request.refresh_from_db()
    _retime_note(
        _latest_note(membership_request=membership_request, action_type="request_created"),
        requested_at,
    )
    _retime_log(
        _latest_log(membership_request=membership_request, action=MembershipLog.Action.requested),
        requested_at,
    )

    for reviewer_username, note_action, message, note_at in review_notes:
        note = _add_review_note(
            membership_request=membership_request,
            reviewer_username=reviewer_username,
            note_action=note_action,
            message=message,
        )
        _retime_note(note, note_at)

    if final_state == MembershipRequest.Status.pending:
        return membership_request

    action_at = final_action_at or requested_at

    if final_state == MembershipRequest.Status.approved:
        approval_log = approve_membership_request(
            membership_request=membership_request,
            actor_username=final_actor_username,
            send_approved_email=False,
            decided_at=action_at,
        )
        _retime_log(approval_log, action_at)
        _retime_note(
            _latest_note(membership_request=membership_request, action_type="request_approved"),
            action_at,
        )
        if approved_expires_at is not None:
            membership = Membership.objects.get(
                target_username=requested_username,
                target_organization=requested_organization,
                membership_type=membership_type,
            )
            Membership.objects.filter(pk=membership.pk).update(created_at=action_at, expires_at=approved_expires_at)
        membership_request.refresh_from_db()
        return membership_request

    if final_state == MembershipRequest.Status.rejected:
        rejection_log, _email_error = reject_membership_request(
            membership_request=membership_request,
            actor_username=final_actor_username,
            rejection_reason=rejection_reason,
            send_rejected_email=False,
            decided_at=action_at,
        )
        _retime_log(rejection_log, action_at)
        _retime_note(
            _latest_note(membership_request=membership_request, action_type="request_rejected"),
            action_at,
        )
        membership_request.refresh_from_db()
        return membership_request

    if final_state == MembershipRequest.Status.ignored:
        ignored_log = ignore_membership_request(
            membership_request=membership_request,
            actor_username=final_actor_username,
            decided_at=action_at,
        )
        _retime_log(ignored_log, action_at)
        _retime_note(
            _latest_note(membership_request=membership_request, action_type="request_ignored"),
            action_at,
        )
        membership_request.refresh_from_db()
        return membership_request

    if final_state not in {MembershipRequest.Status.on_hold, "rfi_followup_pending"}:
        raise ValueError(f"Unsupported workflow final_state={final_state}")

    on_hold_log, _email_error = put_membership_request_on_hold(
        membership_request=membership_request,
        actor_username=final_actor_username,
        rfi_message=rfi_message,
        send_rfi_email=False,
        application_url=application_url,
        held_at=action_at,
    )
    _retime_log(on_hold_log, action_at)
    _retime_note(
        _latest_note(membership_request=membership_request, action_type="request_on_hold"),
        action_at,
    )

    if final_state == MembershipRequest.Status.on_hold:
        membership_request.refresh_from_db()
        return membership_request

    if resubmitted_responses is None:
        raise ValueError("resubmitted_responses is required for rfi_followup_pending")

    followup_at = resubmitted_at or action_at
    resubmitted_log = resubmit_membership_request(
        membership_request=membership_request,
        actor_username=requested_username or requested_organization.representative,
        updated_responses=resubmitted_responses,
        resubmitted_at=followup_at,
    )
    _retime_log(resubmitted_log, followup_at)
    _retime_note(
        _latest_note(membership_request=membership_request, action_type="request_resubmitted"),
        followup_at,
    )
    membership_request.refresh_from_db()
    return membership_request


def _add_review_note(
    *,
    membership_request: MembershipRequest,
    reviewer_username: str,
    note_action: WorkflowReviewNoteAction,
    message: str,
) -> Note:
    if note_action == "vote_approve":
        return add_note(
            membership_request=membership_request,
            username=reviewer_username,
            content=message,
            action={"type": "vote", "value": "approve"},
        )
    if note_action == "vote_disapprove":
        return add_note(
            membership_request=membership_request,
            username=reviewer_username,
            content=message,
            action={"type": "vote", "value": "disapprove"},
        )
    if note_action != "message":
        raise ValueError(f"Unsupported note_action={note_action}")
    return add_note(
        membership_request=membership_request,
        username=reviewer_username,
        content=message,
        action=None,
    )


def _latest_note(*, membership_request: MembershipRequest, action_type: str) -> Note:
    note = Note.objects.filter(
        membership_request=membership_request,
        action__type=action_type,
    ).order_by("-pk").first()
    if note is None:
        raise Note.DoesNotExist(action_type)
    return note


def _latest_log(*, membership_request: MembershipRequest, action: str) -> MembershipLog:
    membership_log = MembershipLog.objects.filter(
        membership_request=membership_request,
        action=action,
    ).order_by("-pk").first()
    if membership_log is None:
        raise MembershipLog.DoesNotExist(action)
    return membership_log


def _retime_note(note: Note, when: datetime.datetime) -> None:
    Note.objects.filter(pk=note.pk).update(timestamp=when)
    note.timestamp = when


def _retime_log(membership_log: MembershipLog, when: datetime.datetime) -> None:
    MembershipLog.objects.filter(pk=membership_log.pk).update(created_at=when)
    membership_log.created_at = when