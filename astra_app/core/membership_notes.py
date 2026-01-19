from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.models import MembershipRequest, Note

# Internal username used for system-authored membership-request notes.
CUSTOS: str = "-"


def add_note(
    *,
    membership_request: MembershipRequest,
    username: str,
    content: str | None = None,
    action: dict[str, Any] | None = None,
) -> Note:
    """Create and persist a validated Note.

    This centralizes normalization + validation (e.g. content/action presence).
    """

    note = Note(
        membership_request=membership_request,
        username=str(username).strip(),
        content=content,
        action=action,
    )
    note.full_clean()
    note.save()
    return note


def tally_last_votes(notes: Iterable[Note]) -> tuple[int, int]:
    """Return (approvals, disapprovals) counting only each user's last vote.

    Votes are represented as Note.action dicts like:
    {"type": "vote", "value": "approve"|"disapprove"}

    If a user votes multiple times, only their last vote counts.
    """

    ordered = sorted(
        notes,
        key=lambda n: (
            n.timestamp,
            0 if n.pk is None else int(n.pk),
        ),
    )

    last_vote_by_user: dict[str, str] = {}
    for note in ordered:
        action = note.action
        if not isinstance(action, dict):
            continue
        if action.get("type") != "vote":
            continue

        value = action.get("value")
        if not isinstance(value, str):
            continue

        value_norm = value.strip().lower()
        if value_norm not in {"approve", "disapprove"}:
            continue

        username = str(note.username or "").strip()
        if not username:
            continue

        last_vote_by_user[username.lower()] = value_norm

    approvals = sum(1 for v in last_vote_by_user.values() if v == "approve")
    disapprovals = sum(1 for v in last_vote_by_user.values() if v == "disapprove")
    return approvals, disapprovals


def note_action_label(action: dict[str, Any]) -> str:
    """Human label for a Note.action payload.

    This is used by templates; keep it conservative for unknown action payloads.
    """

    action_type = action.get("type")
    if action_type == "vote":
        value = str(action.get("value") or "").strip().lower()
        if value == "approve":
            return "Voted approve"
        if value == "disapprove":
            return "Voted disapprove"
        return "Voted"

    if action_type == "request_created":
        return "Request created"
    if action_type == "request_approved":
        return "Request approved"
    if action_type == "request_rejected":
        return "Request rejected"
    if action_type == "request_ignored":
        return "Request ignored"
    if action_type == "request_on_hold":
        return "Request on hold"
    if action_type == "request_resubmitted":
        return "Request resubmitted"
    if action_type == "request_rescinded":
        return "Request rescinded"
    if action_type == "contacted":
        return "User contacted"

    if action_type == "representative_changed":
        old = str(action.get("old") or "").strip()
        new = str(action.get("new") or "").strip()
        if old and new:
            return f"Representative changed from {old} to {new}"
        return "Representative changed"

    return str(action_type or "Action")


def note_action_icon(action: dict[str, Any]) -> str:
    """Font Awesome icon class (without style prefix) for a Note.action payload."""

    action_type = action.get("type")
    if action_type == "vote":
        value = str(action.get("value") or "").strip().lower()
        if value == "approve":
            return "fa-thumbs-up"
        if value == "disapprove":
            return "fa-thumbs-down"
        return "fa-thumbs-up"

    if action_type == "contacted":
        return "fa-envelope"

    if action_type == "request_approved":
        return "fa-circle-check"
    if action_type == "request_rejected":
        return "fa-circle-xmark"
    if action_type == "request_ignored":
        return "fa-ghost"
    if action_type == "request_created":
        return "fa-hand"
    if action_type == "request_on_hold":
        return "fa-circle-pause"
    if action_type == "request_resubmitted":
        return "fa-rotate-right"
    if action_type == "request_rescinded":
        return "fa-ban"

    if action_type == "representative_changed":
        return "fa-user-check"

    return "fa-bolt"
