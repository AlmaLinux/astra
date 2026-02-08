import hashlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from django import template
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import SafeString, mark_safe
from post_office.models import Email

from core.backends import FreeIPAUser
from core.membership_notes import CUSTOS, note_action_icon, note_action_label, tally_last_votes
from core.models import MembershipRequest, Note

register = template.Library()


_BUBBLE_BG_COLORS: list[str] = [
    # Light, readable colors. Keep these fairly pastel to avoid jarring UI.
    "#BBDEFB",
    "#C8E6C9",
    "#FFE0B2",
    "#E1BEE7",
    "#B2EBF2",
    "#FFF9C4",
    "#F8BBD0",
    "#D1C4E9",
    "#C5CAE9",
    "#DCEDC8",
    "#F0F4C3",
    "#B2DFDB",
    "#FFECB3",
    "#FFCCBC",
    "#D7CCC8",
    "#CFD8DC",
    "#B3E5FC",
    "#C5E1A5",
    "#FFCDD2",
    "#E6EE9C",
]


def _relative_luminance_from_hex(hex_color: str) -> float:
    v = hex_color.lstrip("#")
    if len(v) != 6:
        return 1.0

    r = int(v[0:2], 16) / 255.0
    g = int(v[2:4], 16) / 255.0
    b = int(v[4:6], 16) / 255.0

    def to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = to_linear(r), to_linear(g), to_linear(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def _pick_foreground_for_background(bg_hex: str) -> str:
    # Choose foreground with better WCAG contrast against the background.
    bg_l = _relative_luminance_from_hex(bg_hex)
    white_l = 1.0
    blackish_l = 0.0

    contrast_white = (white_l + 0.05) / (bg_l + 0.05)
    contrast_black = (bg_l + 0.05) / (blackish_l + 0.05)
    return "#ffffff" if contrast_white >= contrast_black else "#212529"


def _bubble_style_for_username(username: str) -> str:
    # Built-in hash() is randomized per-process; use a stable digest.
    digest = hashlib.blake2s(username.encode("utf-8"), digest_size=2).digest()
    idx = int.from_bytes(digest, "big") % len(_BUBBLE_BG_COLORS)
    bg = _BUBBLE_BG_COLORS[idx]
    fg = _pick_foreground_for_background(bg)
    return f"--bubble-bg: {bg}; --bubble-fg: {fg};"


def _timeline_dom_id(key: str) -> str:
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=4).hexdigest()
    return f"timeline-{digest}"


def _email_id_from_action(action: dict[str, Any] | None) -> int | None:
    if not isinstance(action, dict):
        return None
    raw = action.get("email_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _email_modal_id(email_id: int) -> str:
    return f"membership-email-modal-{email_id}"


def _split_emails(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _render_email_contents(email: Email) -> tuple[str, str, str]:
    return (
        str(email.subject or ""),
        str(email.html_message or ""),
        str(email.message or ""),
    )


def _email_modals_for_notes(notes: list[Note]) -> list[dict[str, Any]]:
    email_ids = []
    seen: set[int] = set()
    for note in notes:
        email_id = _email_id_from_action(note.action)
        if email_id is None or email_id in seen:
            continue
        seen.add(email_id)
        email_ids.append(email_id)

    if not email_ids:
        return []

    emails = list(
        Email.objects.filter(pk__in=email_ids).select_related("template").prefetch_related("logs")
    )
    emails_by_id = {email.id: email for email in emails}

    modals: list[dict[str, Any]] = []
    for email_id in email_ids:
        email = emails_by_id.get(email_id)
        if email is None:
            continue

        subject, html, text = _render_email_contents(email)

        headers = email.headers if isinstance(email.headers, dict) else {}
        reply_to = str(headers.get("Reply-To") or "").strip()
        other_headers = [
            (str(k), str(v))
            for k, v in headers.items()
            if str(k).strip().lower() != "reply-to" and str(v).strip()
        ]

        logs_raw = list(email.logs.all())
        logs_sorted = sorted(
            logs_raw,
            key=lambda log: (
                log.date,
                0 if log.pk is None else int(log.pk),
            ),
        )
        logs = [
            {
                "date": log.date,
                "status": log.get_status_display(),
                "message": str(log.message or ""),
                "exception_type": str(log.exception_type or ""),
            }
            for log in logs_sorted
        ]

        modals.append(
            {
                "email_id": email.id,
                "modal_id": _email_modal_id(email.id),
                "from_email": str(email.from_email or ""),
                "to": _split_emails(email.to),
                "cc": _split_emails(email.cc),
                "bcc": _split_emails(email.bcc),
                "reply_to": reply_to,
                "headers": other_headers,
                "subject": subject,
                "html": html,
                "text": text,
                "logs": logs,
            }
        )

    return modals


def _current_username_from_request(http_request: HttpRequest | None) -> str:
    if http_request is None or getattr(http_request, "user", None) is None:
        return ""
    try:
        return str(http_request.user.get_username() or "").strip()
    except Exception:
        return ""


def _avatar_users_by_username(notes: list[Note]) -> dict[str, object]:
    avatar_users_by_username: dict[str, object] = {}
    for username in {n.username for n in notes if n.username and n.username != CUSTOS}:
        user_obj = FreeIPAUser.get(username)
        if user_obj is not None:
            avatar_users_by_username[username] = user_obj
    return avatar_users_by_username


def _note_display_username(note: Note) -> str:
    if note.username == CUSTOS:
        return "Astra Custodia"
    return note.username


def _custos_bubble_style() -> str:
    # Similar to action grey, but slightly darker.
    return "--bubble-bg: #e9ecef; --bubble-fg: #212529;"


def _timeline_entries_for_notes(
    notes: list[Note],
    *,
    current_username: str,
    email_modal_ids: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    avatar_users_by_username = _avatar_users_by_username(notes)
    entries: list[dict[str, Any]] = []
    for n in notes:
        is_self = current_username and n.username.lower() == current_username.lower()
        avatar_user = avatar_users_by_username.get(n.username)
        is_custos = n.username == CUSTOS
        display_username = _note_display_username(n)

        membership_request_id = n.membership_request_id
        membership_request_url = reverse("membership-request-detail", args=[membership_request_id])

        if isinstance(n.action, dict) and n.action:
            label = note_action_label(n.action)
            icon = note_action_icon(n.action)
            entry: dict[str, Any] = {
                "kind": "action",
                "note": n,
                "label": label,
                "icon": icon,
                "is_self": is_self,
                "avatar_user": avatar_user,
                "bubble_style": "--bubble-bg: #f8f9fa; --bubble-fg: #212529;",
                "is_custos": is_custos,
                "display_username": display_username,
                "membership_request_id": membership_request_id,
                "membership_request_url": membership_request_url,
            }

            email_id = _email_id_from_action(n.action)
            if email_id is not None and email_modal_ids is not None:
                modal_id = email_modal_ids.get(email_id)
                if modal_id:
                    entry["email_modal_id"] = modal_id

            entries.append(
                entry
            )

        if n.content is not None and str(n.content).strip() != "":
            bubble_style: str | None = None
            if not is_self and n.username:
                if is_custos:
                    bubble_style = _custos_bubble_style()
                else:
                    bubble_style = _bubble_style_for_username(n.username.strip().lower())

            entries.append(
                {
                    "kind": "message",
                    "note": n,
                    "is_self": is_self,
                    "avatar_user": avatar_user,
                    "bubble_style": bubble_style,
                    "is_custos": is_custos,
                    "display_username": display_username,
                    "membership_request_id": membership_request_id,
                    "membership_request_url": membership_request_url,
                }
            )

    return entries


def _group_timeline_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive entries by the same author within a short time window.

    This is purely a presentation concern: it reduces repeated avatars + headers
    when someone performs several actions in quick succession.

    Grouping rules:
    - Only consecutive entries are eligible.
    - Same author (note.username, case-insensitive) and same alignment (is_self).
    - If membership_request_id is present (aggregate views), it must match.
    - Timestamps must be within 60 seconds of the previous entry.
    """

    max_gap = timedelta(seconds=60)
    groups: list[dict[str, Any]] = []

    current: dict[str, Any] | None = None
    last_ts = None

    for entry in entries:
        note: Note = entry["note"]
        username = str(note.username or "").strip().lower()
        is_self = bool(entry.get("is_self", False))
        mr_id = entry.get("membership_request_id")

        ts = note.timestamp

        if current is None:
            current = {
                "username": username,
                "is_self": is_self,
                "membership_request_id": mr_id,
                "header_entry": entry,
                "entries": [entry],
            }
            last_ts = ts
            continue

        same_author = current["username"] == username
        same_side = current["is_self"] == is_self
        same_request = current.get("membership_request_id") == mr_id
        within_gap = (ts - last_ts) <= max_gap if last_ts is not None else False

        if same_author and same_side and same_request and within_gap:
            current["entries"].append(entry)
            last_ts = ts
        else:
            groups.append(current)
            current = {
                "username": username,
                "is_self": is_self,
                "membership_request_id": mr_id,
                "header_entry": entry,
                "entries": [entry],
            }
            last_ts = ts

    if current is not None:
        groups.append(current)

    return groups


@register.simple_tag(takes_context=True)
def membership_notes_aggregate_for_user(
    context: dict[str, Any],
    username: str,
    *,
    compact: bool = True,
    next_url: str | None = None,
) -> SafeString | str:
    request = context.get("request")
    http_request = request if isinstance(request, HttpRequest) else None

    membership_can_view = bool(context.get("membership_can_view", False))
    if not membership_can_view:
        return ""

    normalized_username = str(username or "").strip()
    if not normalized_username:
        return ""

    notes = list(
        Note.objects.filter(membership_request__requested_username=normalized_username).order_by("timestamp", "pk")
    )
    approvals, disapprovals = tally_last_votes(notes)

    dom_id = _timeline_dom_id(f"user:{normalized_username}")
    dummy_request = SimpleNamespace(pk=dom_id)

    resolved_next_url = next_url
    if resolved_next_url is None:
        resolved_next_url = http_request.get_full_path() if http_request is not None else ""

    post_url = reverse("membership-notes-aggregate-note-add")

    email_modals = _email_modals_for_notes(notes)
    email_modal_ids = {m["email_id"]: m["modal_id"] for m in email_modals}

    html = render_to_string(
        "core/_membership_notes.html",
        {
            "compact": compact,
            "membership_request": dummy_request,
            "groups": _group_timeline_entries(
                _timeline_entries_for_notes(
                    notes,
                    current_username=_current_username_from_request(http_request),
                    email_modal_ids=email_modal_ids,
                )
            ),
            "note_count": len(notes),
            "approvals": approvals,
            "disapprovals": disapprovals,
            "can_vote": False,
            "post_url": post_url,
            "aggregate_target_type": "user",
            "aggregate_target": normalized_username,
            "next_url": resolved_next_url,
            "email_modals": email_modals,
        },
        request=http_request,
    )
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def membership_notes_aggregate_for_organization(
    context: dict[str, Any],
    organization_id: int,
    *,
    compact: bool = True,
    next_url: str | None = None,
) -> SafeString | str:
    request = context.get("request")
    http_request = request if isinstance(request, HttpRequest) else None

    membership_can_view = bool(context.get("membership_can_view", False))
    if not membership_can_view:
        return ""

    if not organization_id:
        return ""

    notes = list(
        Note.objects.filter(membership_request__requested_organization_id=organization_id).order_by(
            "timestamp", "pk"
        )
    )
    approvals, disapprovals = tally_last_votes(notes)

    dom_id = _timeline_dom_id(f"org:{organization_id}")
    dummy_request = SimpleNamespace(pk=dom_id)

    resolved_next_url = next_url
    if resolved_next_url is None:
        resolved_next_url = http_request.get_full_path() if http_request is not None else ""

    post_url = reverse("membership-notes-aggregate-note-add")

    email_modals = _email_modals_for_notes(notes)
    email_modal_ids = {m["email_id"]: m["modal_id"] for m in email_modals}

    html = render_to_string(
        "core/_membership_notes.html",
        {
            "compact": compact,
            "membership_request": dummy_request,
            "groups": _group_timeline_entries(
                _timeline_entries_for_notes(
                    notes,
                    current_username=_current_username_from_request(http_request),
                    email_modal_ids=email_modal_ids,
                )
            ),
            "note_count": len(notes),
            "approvals": approvals,
            "disapprovals": disapprovals,
            "can_vote": False,
            "post_url": post_url,
            "aggregate_target_type": "org",
            "aggregate_target": str(organization_id),
            "next_url": resolved_next_url,
            "email_modals": email_modals,
        },
        request=http_request,
    )
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def membership_notes(
    context: dict[str, Any],
    membership_request: MembershipRequest | int,
    *,
    compact: bool = False,
    next_url: str | None = None,
) -> SafeString | str:
    request = context.get("request")
    http_request = request if isinstance(request, HttpRequest) else None

    mr: MembershipRequest | None
    if isinstance(membership_request, MembershipRequest):
        mr = membership_request
    else:
        mr = MembershipRequest.objects.select_related("membership_type", "requested_organization").filter(pk=membership_request).first()

    if mr is None:
        return ""

    notes = list(Note.objects.filter(membership_request_id=mr.pk).order_by("timestamp", "pk"))
    approvals, disapprovals = tally_last_votes(notes)

    resolved_next_url = next_url
    if resolved_next_url is None:
        resolved_next_url = http_request.get_full_path() if http_request is not None else ""

    current_username = _current_username_from_request(http_request)

    membership_can_add = bool(context.get("membership_can_add", False))
    membership_can_change = bool(context.get("membership_can_change", False))
    membership_can_delete = bool(context.get("membership_can_delete", False))
    can_vote = membership_can_add or membership_can_change or membership_can_delete

    post_url = reverse("membership-request-note-add", args=[mr.pk])

    email_modals = _email_modals_for_notes(notes)
    email_modal_ids = {m["email_id"]: m["modal_id"] for m in email_modals}

    entries = _timeline_entries_for_notes(
        notes,
        current_username=current_username,
        email_modal_ids=email_modal_ids,
    )

    html = render_to_string(
        "core/_membership_notes.html",
        {
            "compact": compact,
            "membership_request": mr,
            "groups": _group_timeline_entries(entries),
            "note_count": len(notes),
            "approvals": approvals,
            "disapprovals": disapprovals,
            "can_vote": can_vote,
            "post_url": post_url,
            "next_url": resolved_next_url,
            "email_modals": email_modals,
        },
        request=http_request,
    )
    return mark_safe(html)
