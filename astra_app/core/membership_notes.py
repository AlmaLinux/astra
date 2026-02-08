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


# Maps action_type -> (label, icon).  Vote sub-logic is handled inline.
_ACTION_DISPLAY: dict[str, tuple[str, str]] = {
    "request_created": ("Request created", "fa-hand"),
    "request_approved": ("Request approved", "fa-circle-check"),
    "request_rejected": ("Request rejected", "fa-circle-xmark"),
    "request_ignored": ("Request ignored", "fa-ghost"),
    "request_on_hold": ("Request on hold", "fa-circle-pause"),
    "request_resubmitted": ("Request resubmitted", "fa-rotate-right"),
    "request_rescinded": ("Request rescinded", "fa-ban"),
    "contacted": ("User contacted", "fa-envelope"),
}

_VOTE_LABELS: dict[str, str] = {"approve": "Voted approve", "disapprove": "Voted disapprove"}
_VOTE_ICONS: dict[str, str] = {"approve": "fa-thumbs-up", "disapprove": "fa-thumbs-down"}

_CONTACTED_LABELS: dict[str, str] = {
    "approved": "Approval email sent",
    "accepted": "Approval email sent",
    "rejected": "Rejection email sent",
    "rfi": "RFI email sent",
    "on_hold": "RFI email sent",
}


def note_action_label(action: dict[str, Any]) -> str:
    """Human label for a Note.action payload.

    This is used by templates; keep it conservative for unknown action payloads.
    """
    action_type = action.get("type")

    if action_type == "vote":
        value = str(action.get("value") or "").strip().lower()
        return _VOTE_LABELS.get(value, "Voted")

    if action_type == "contacted":
        kind = str(action.get("kind") or "").strip().lower()
        return _CONTACTED_LABELS.get(kind, "User contacted")

    if action_type == "representative_changed":
        old = str(action.get("old") or "").strip()
        new = str(action.get("new") or "").strip()
        if old and new:
            return f"Representative changed from {old} to {new}"
        return "Representative changed"

    label, _ = _ACTION_DISPLAY.get(action_type, (None, None))
    return label or str(action_type or "Action")


def note_action_icon(action: dict[str, Any]) -> str:
    """Font Awesome icon class (without style prefix) for a Note.action payload."""
    action_type = action.get("type")

    if action_type == "vote":
        value = str(action.get("value") or "").strip().lower()
        return _VOTE_ICONS.get(value, "fa-thumbs-up")

    if action_type == "representative_changed":
        return "fa-user-check"

    _, icon = _ACTION_DISPLAY.get(action_type, (None, None))
    return icon or "fa-bolt"
