import hashlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlencode

from django import template
from django.conf import settings
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.functional import SimpleLazyObject, empty
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe
from markdownify.templatetags.markdownify import markdownify as render_markdown
from post_office.models import Email

from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS, last_votes, note_action_icon, note_action_label
from core.models import MembershipRequest, Note
from core.tokens import make_membership_notes_aggregate_target_token
from core.views_utils import get_username, try_get_username_from_user

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
_MIRROR_VALIDATION_NOTE_HEADER = "Mirror validation summary"
_MIRROR_VALIDATION_BOLD_LABELS = {
    "Domain",
    "Mirror status",
    "AlmaLinux mirror network",
    "GitHub pull request",
}
_MEMBERSHIP_NOTE_MARKDOWN_SETTINGS = "membership_note"


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

        recipient_count = (
            len(_split_emails(email.to))
            + len(_split_emails(email.cc))
            + len(_split_emails(email.bcc))
        )
        recipient_delivery_summary = "No aggregate recipient status recorded."
        if email.recipient_delivery_status is not None:
            recipient_delivery_summary = email.get_recipient_delivery_status_display()

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
                "recipient_delivery_summary": recipient_delivery_summary,
                "recipient_delivery_summary_note": (
                    "Single rolled-up status across all recipients. Individual recipient outcomes may differ."
                    if recipient_count > 1
                    else ""
                ),
                "logs": logs,
            }
        )

    return modals


def _current_username_from_request(http_request: HttpRequest | None) -> str:
    if http_request is None:
        return ""
    return get_username(http_request, allow_user_fallback=False)


def _normalize_username(value: object) -> str:
    return str(value or "").strip().lower()


def _aggregate_author_usernames(notes: list[Note]) -> set[str]:
    return {
        _normalize_username(note.username)
        for note in notes
        if note.username and note.username != CUSTOS and _normalize_username(note.username)
    }


def _avatar_safe_aggregate_user(user_obj: object | None) -> object | None:
    if user_obj is None:
        return None

    if isinstance(user_obj, SimpleLazyObject):
        # Only reuse an already-evaluated lazy user so this optimization never forces a new fetch.
        wrapped_user = user_obj._wrapped
        if wrapped_user is empty or wrapped_user is user_obj:
            return None
        if isinstance(wrapped_user, FreeIPAUser):
            return wrapped_user
        return None

    if isinstance(user_obj, FreeIPAUser):
        return user_obj

    candidate_user = cast(Any, user_obj)
    if not hasattr(candidate_user, "is_authenticated") or not bool(candidate_user.is_authenticated):
        return None
    if not hasattr(candidate_user, "username") or not hasattr(candidate_user, "email"):
        return None
    if not hasattr(candidate_user, "get_username") or not callable(candidate_user.get_username):
        return None

    username = _normalize_username(try_get_username_from_user(candidate_user))
    email = str(candidate_user.email or "").strip()
    request_get_username = _normalize_username(candidate_user.get_username())
    if not username or not email or request_get_username != username:
        return None

    return candidate_user


def _avatar_safe_request_user_for_aggregate_notes(http_request: HttpRequest | None) -> object | None:
    if http_request is None or not hasattr(http_request, "user"):
        return None

    return _avatar_safe_aggregate_user(http_request.user)


def _aggregate_preloaded_target_user(
    *,
    context: dict[str, Any],
    aggregate_target_type: str,
    aggregate_target: str,
) -> object | None:
    if aggregate_target_type != "user":
        return None

    normalized_target = _normalize_username(aggregate_target)
    if not normalized_target:
        return None

    for candidate in (context.get("fu"), context.get("aggregate_preloaded_target_user")):
        safe_candidate = _avatar_safe_aggregate_user(candidate)
        if safe_candidate is None:
            continue
        candidate_username = _normalize_username(try_get_username_from_user(safe_candidate))
        if candidate_username == normalized_target:
            return safe_candidate

    return None


def _preloaded_aggregate_avatar_users_by_username(
    *,
    context: dict[str, Any],
    notes: list[Note],
    http_request: HttpRequest | None,
    aggregate_target_user: object | None,
) -> dict[str, object]:
    aggregate_author_usernames = _aggregate_author_usernames(notes)
    if not aggregate_author_usernames:
        return {}

    preloaded_users_by_username: dict[str, object] = {}

    target_user = _avatar_safe_aggregate_user(aggregate_target_user)
    if target_user is not None:
        target_username = _normalize_username(try_get_username_from_user(target_user))
        if target_username in aggregate_author_usernames:
            preloaded_users_by_username[target_username] = target_user

    request_user = _avatar_safe_request_user_for_aggregate_notes(http_request)
    if request_user is not None:
        request_username = _normalize_username(try_get_username_from_user(request_user))
        if request_username in aggregate_author_usernames and request_username not in preloaded_users_by_username:
            preloaded_users_by_username[request_username] = request_user

    return preloaded_users_by_username


def _avatar_users_by_username(notes: list[Note]) -> dict[str, object]:
    avatar_users_by_username: dict[str, object] = {}
    for username in {str(n.username or "").strip() for n in notes if n.username and n.username != CUSTOS}:
        user_obj = FreeIPAUser.get(username)
        if user_obj is not None:
            avatar_users_by_username[username.lower()] = user_obj
    return avatar_users_by_username


def _aggregate_avatar_users_by_username(
    notes: list[Note],
    *,
    preloaded_users_by_username: dict[str, object] | None = None,
) -> dict[str, object]:
    lookup_usernames = _aggregate_author_usernames(notes)
    if not lookup_usernames:
        return {}

    resolved_users_by_username = dict(preloaded_users_by_username or {})
    unresolved_usernames = lookup_usernames - set(resolved_users_by_username)
    if unresolved_usernames:
        resolved_users_by_username.update(FreeIPAUser.find_lightweight_by_usernames(unresolved_usernames))
    return resolved_users_by_username


def _note_display_username(note: Note) -> str:
    if note.username == CUSTOS:
        return "Astra Custodia"
    return note.username


def _custos_bubble_style() -> str:
    # Similar to action grey, but slightly darker.
    return "--bubble-bg: #e9ecef; --bubble-fg: #212529;"


def _render_membership_note_markdown(content: str) -> SafeString:
    # Escape first so markdown syntax is supported without trusting inline HTML.
    rendered = render_markdown(escape(content), custom_settings=_MEMBERSHIP_NOTE_MARKDOWN_SETTINGS)
    if rendered.startswith("<p>") and rendered.endswith("</p>"):
        inner = rendered.removeprefix("<p>").removesuffix("</p>")
        if "<p>" not in inner and "</p>" not in inner:
            return mark_safe(inner)
    return rendered


def _render_note_content(note: Note) -> SafeString | str:
    content = "" if note.content is None else str(note.content)
    if note.username != CUSTOS or not content.startswith(_MIRROR_VALIDATION_NOTE_HEADER):
        return _render_membership_note_markdown(content)

    rendered_lines: list[SafeString] = []
    for line in content.splitlines():
        label, separator, value = line.partition(": ")
        if separator and label in _MIRROR_VALIDATION_BOLD_LABELS and value:
            rendered_lines.append(format_html("{}: <strong>{}</strong>", label, value))
            continue
        rendered_lines.append(format_html("{}", line))

    return format_html_join(mark_safe("<br>"), "{}", ((line,) for line in rendered_lines))


def _normalize_response_snapshot(snapshot: object) -> list[dict[str, str]]:
    if not isinstance(snapshot, list):
        return []

    normalized: list[dict[str, str]] = []
    for row in snapshot:
        if not isinstance(row, dict):
            continue

        normalized_row: dict[str, str] = {}
        for key, value in row.items():
            question = str(key).strip()
            if not question:
                continue
            normalized_row[question] = "" if value is None else str(value)

        if normalized_row:
            normalized.append(normalized_row)

    return normalized


def _old_responses_snapshot_from_action(action: object) -> list[dict[str, str]] | None:
    if not isinstance(action, dict):
        return None
    if "old_responses" not in action:
        return None
    return _normalize_response_snapshot(action.get("old_responses"))


def _response_snapshot_value_map(snapshot: list[dict[str, str]]) -> tuple[list[str], dict[str, str]]:
    order: list[str] = []
    values_by_question: dict[str, str] = {}
    for row in snapshot:
        for question, value in row.items():
            if question not in values_by_question:
                order.append(question)
            values_by_question[question] = value
    return order, values_by_question


def _request_resubmitted_diff_rows(
    *,
    old_snapshot: list[dict[str, str]],
    new_snapshot: list[dict[str, str]],
) -> list[dict[str, str]]:
    old_order, old_values = _response_snapshot_value_map(old_snapshot)
    new_order, new_values = _response_snapshot_value_map(new_snapshot)

    merged_order = old_order + [question for question in new_order if question not in old_values]
    rows: list[dict[str, str]] = []
    for question in merged_order:
        old_value = old_values.get(question, "")
        new_value = new_values.get(question, "")
        if old_value == new_value:
            continue
        rows.append(
            {
                "question": question,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return rows


def _membership_request_responses_by_id(notes: list[Note]) -> dict[int, list[dict[str, str]]]:
    request_ids = sorted({int(n.membership_request_id) for n in notes if n.membership_request_id is not None})
    if not request_ids:
        return {}

    responses_by_id: dict[int, list[dict[str, str]]] = {}
    for membership_request_id, responses in MembershipRequest.objects.filter(pk__in=request_ids).values_list("pk", "responses"):
        responses_by_id[int(membership_request_id)] = _normalize_response_snapshot(responses)
    return responses_by_id


def _request_resubmitted_new_snapshots_by_note_id(
    notes: list[Note],
    *,
    current_responses_by_request_id: dict[int, list[dict[str, str]]],
) -> dict[int, list[dict[str, str]]]:
    notes_by_request_id: dict[int, list[Note]] = {}
    for note in notes:
        if note.membership_request_id is None:
            continue
        action = note.action
        if not isinstance(action, dict):
            continue
        if action.get("type") != "request_resubmitted":
            continue
        if _old_responses_snapshot_from_action(action) is None:
            continue
        notes_by_request_id.setdefault(int(note.membership_request_id), []).append(note)

    snapshots_by_note_id: dict[int, list[dict[str, str]]] = {}
    for membership_request_id, request_notes in notes_by_request_id.items():
        for index, note in enumerate(request_notes):
            if note.pk is None:
                continue

            next_note = request_notes[index + 1] if index + 1 < len(request_notes) else None
            if next_note is not None:
                next_snapshot = _old_responses_snapshot_from_action(next_note.action)
                if next_snapshot is None:
                    continue
                snapshots_by_note_id[int(note.pk)] = next_snapshot
                continue

            snapshots_by_note_id[int(note.pk)] = current_responses_by_request_id.get(membership_request_id, [])

    return snapshots_by_note_id


def _timeline_entries_for_notes(
    notes: list[Note],
    *,
    current_username: str,
    email_modal_ids: dict[int, str] | None = None,
    request_resubmitted_new_snapshots_by_note_id: dict[int, list[dict[str, str]]] | None = None,
    avatar_users_by_username: dict[str, object] | None = None,
) -> list[dict[str, Any]]:
    if avatar_users_by_username is None:
        avatar_users_by_username = _avatar_users_by_username(notes)
    entries: list[dict[str, Any]] = []
    for n in notes:
        is_self = current_username and n.username.lower() == current_username.lower()
        avatar_user = avatar_users_by_username.get(str(n.username or "").strip().lower())
        is_custos = n.username == CUSTOS
        display_username = _note_display_username(n)

        membership_request_id = n.membership_request_id
        membership_request_url = reverse("membership-request-detail", args=[membership_request_id])

        if isinstance(n.action, dict) and n.action:
            action = n.action
            label = note_action_label(action)
            icon = note_action_icon(action)
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

            if (
                settings.MEMBERSHIP_NOTES_RESUBMITTED_DIFFS_ENABLED
                and action.get("type") == "request_resubmitted"
                and n.pk is not None
                and request_resubmitted_new_snapshots_by_note_id is not None
            ):
                old_snapshot = _old_responses_snapshot_from_action(action)
                new_snapshot = request_resubmitted_new_snapshots_by_note_id.get(int(n.pk))
                if old_snapshot is not None and new_snapshot is not None:
                    diff_rows = _request_resubmitted_diff_rows(
                        old_snapshot=old_snapshot,
                        new_snapshot=new_snapshot,
                    )
                    if diff_rows:
                        entry["request_resubmitted_diff_rows"] = diff_rows

            email_id = _email_id_from_action(action)
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
                    "rendered_content": _render_note_content(n),
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


def _render_membership_notes_aggregate(
    *,
    context: dict[str, Any],
    notes: list[Note] | None,
    dom_key: str,
    compact: bool,
    next_url: str | None,
    aggregate_target_type: str,
    aggregate_target: str,
) -> SafeString:
    request = context.get("request")
    http_request = request if isinstance(request, HttpRequest) else None
    membership_can_add = bool(context.get("membership_can_add", False))
    membership_can_change = bool(context.get("membership_can_change", False))
    membership_can_delete = bool(context.get("membership_can_delete", False))
    can_write = membership_can_add or membership_can_change or membership_can_delete
    resolved_notes = None if notes is None else list(notes)

    dummy_request = SimpleNamespace(pk=_timeline_dom_id(dom_key))

    resolved_next_url = next_url
    if resolved_next_url is None:
        resolved_next_url = http_request.get_full_path() if http_request is not None else ""

    post_url = reverse("api-membership-notes-aggregate-add")
    aggregate_target_user = _aggregate_preloaded_target_user(
        context=context,
        aggregate_target_type=aggregate_target_type,
        aggregate_target=aggregate_target,
    )
    aggregate_preloaded_target_token = ""
    if aggregate_target_user is not None:
        safe_target_user = cast(Any, aggregate_target_user)
        aggregate_preloaded_target_username = str(try_get_username_from_user(aggregate_target_user) or "").strip()
        if aggregate_preloaded_target_username:
            aggregate_preloaded_target_token = make_membership_notes_aggregate_target_token(
                {
                    "target_type": aggregate_target_type,
                    "target": aggregate_preloaded_target_username,
                    "email": str(safe_target_user.email or "").strip(),
                }
            )

    note_summary_url = ""
    note_detail_url = ""
    groups: list[dict[str, Any]] = []
    note_count = 0
    approvals = 0
    disapprovals = 0
    aggregate_note_query = urlencode(
        {
            "target_type": aggregate_target_type,
            "target": aggregate_target,
        }
    )
    note_summary_url = reverse("api-membership-notes-aggregate-summary") + "?" + aggregate_note_query
    note_detail_url = reverse("api-membership-notes-aggregate") + "?" + aggregate_note_query

    has_fallback_content = resolved_notes is not None
    if resolved_notes is not None:
        votes_by_user = last_votes(resolved_notes)
        approvals = sum(1 for vote in votes_by_user.values() if vote == "approve")
        disapprovals = sum(1 for vote in votes_by_user.values() if vote == "disapprove")
        current_responses_by_request_id = _membership_request_responses_by_id(resolved_notes)
        request_resubmitted_new_snapshots_by_note_id = _request_resubmitted_new_snapshots_by_note_id(
            resolved_notes,
            current_responses_by_request_id=current_responses_by_request_id,
        )
        preloaded_users_by_username = _preloaded_aggregate_avatar_users_by_username(
            context=context,
            notes=resolved_notes,
            http_request=http_request,
            aggregate_target_user=aggregate_target_user,
        )
        groups = _group_timeline_entries(
            _timeline_entries_for_notes(
                resolved_notes,
                current_username=_current_username_from_request(http_request),
                request_resubmitted_new_snapshots_by_note_id=request_resubmitted_new_snapshots_by_note_id,
                avatar_users_by_username=_aggregate_avatar_users_by_username(
                    resolved_notes,
                    preloaded_users_by_username=preloaded_users_by_username,
                ),
            )
        )
        note_count = len(resolved_notes)

    html = render_to_string(
        "core/_membership_notes.html",
        {
            "compact": compact,
            "membership_request": dummy_request,
            "groups": groups,
            "note_count": note_count,
            "approvals": approvals,
            "disapprovals": disapprovals,
            "current_user_vote": None,
            "can_vote": False,
            "can_write": can_write,
            "has_fallback_content": has_fallback_content,
            "details_loaded": False,
            "post_url": post_url,
            "aggregate_target_type": aggregate_target_type,
            "aggregate_target": aggregate_target,
            "aggregate_preloaded_target_token": aggregate_preloaded_target_token,
            "note_summary_url": note_summary_url,
            "note_detail_url": note_detail_url,
            "next_url": resolved_next_url,
        },
        request=http_request,
    )
    return mark_safe(html)


def _membership_notes_aggregate_for_target(
    context: dict[str, Any],
    *,
    notes_filter: dict[str, Any],
    dom_key: str,
    compact: bool,
    next_url: str | None,
    aggregate_target_type: str,
    aggregate_target: str,
) -> SafeString | str:
    membership_can_view = bool(context.get("membership_can_view", False))
    if not membership_can_view:
        return ""

    return _render_membership_notes_aggregate(
        context=context,
        notes=None,
        dom_key=dom_key,
        compact=compact,
        next_url=next_url,
        aggregate_target_type=aggregate_target_type,
        aggregate_target=aggregate_target,
    )


@register.simple_tag(takes_context=True)
def membership_notes_aggregate_for_user(
    context: dict[str, Any],
    username: str,
    *,
    compact: bool = True,
    next_url: str | None = None,
) -> SafeString | str:
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return ""

    return _membership_notes_aggregate_for_target(
        context=context,
        notes_filter={"membership_request__requested_username": normalized_username},
        dom_key=f"user:{normalized_username}",
        compact=compact,
        next_url=next_url,
        aggregate_target_type="user",
        aggregate_target=normalized_username,
    )


@register.simple_tag(takes_context=True)
def membership_notes_aggregate_for_organization(
    context: dict[str, Any],
    organization_id: int,
    *,
    compact: bool = True,
    next_url: str | None = None,
) -> SafeString | str:
    if not organization_id:
        return ""

    return _membership_notes_aggregate_for_target(
        context=context,
        notes_filter={"membership_request__requested_organization_id": organization_id},
        dom_key=f"org:{organization_id}",
        compact=compact,
        next_url=next_url,
        aggregate_target_type="org",
        aggregate_target=str(organization_id),
    )


@register.simple_tag(takes_context=True)
def render_membership_request_notes(
    context: dict[str, Any],
    membership_request: MembershipRequest | int,
    *,
    compact: bool = False,
    next_url: str | None = None,
    notes: list[Note] | None = None,
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

    resolved_next_url = next_url
    if resolved_next_url is None:
        resolved_next_url = http_request.get_full_path() if http_request is not None else ""

    membership_can_add = bool(context.get("membership_can_add", False))
    membership_can_change = bool(context.get("membership_can_change", False))
    membership_can_delete = bool(context.get("membership_can_delete", False))
    can_vote = membership_can_add or membership_can_change or membership_can_delete
    can_write = can_vote

    post_url = reverse("api-membership-request-notes-add", args=[mr.pk])
    note_summary_url = reverse("api-membership-request-notes-summary", args=[mr.pk])
    note_detail_url = reverse("api-membership-request-notes", args=[mr.pk])

    resolved_notes = list(notes) if notes is not None else []
    approvals = 0
    disapprovals = 0
    current_user_vote = None
    groups: list[dict[str, Any]] = []
    votes_by_user = last_votes(resolved_notes)
    approvals = sum(1 for vote in votes_by_user.values() if vote == "approve")
    disapprovals = sum(1 for vote in votes_by_user.values() if vote == "disapprove")

    current_username = _current_username_from_request(http_request)
    current_user_vote = votes_by_user.get(current_username.lower()) if current_username else None

    request_resubmitted_new_snapshots_by_note_id = _request_resubmitted_new_snapshots_by_note_id(
        resolved_notes,
        current_responses_by_request_id={int(mr.pk): _normalize_response_snapshot(mr.responses)},
    )
    groups = _group_timeline_entries(
        _timeline_entries_for_notes(
            resolved_notes,
            current_username=current_username,
            request_resubmitted_new_snapshots_by_note_id=request_resubmitted_new_snapshots_by_note_id,
        )
    )
    note_count = len(resolved_notes)
    has_fallback_content = notes is not None
    details_loaded = notes is not None

    html = render_to_string(
        "core/_membership_notes.html",
        {
            "compact": compact,
            "membership_request": mr,
            "groups": groups,
            "note_count": note_count,
            "approvals": approvals,
            "disapprovals": disapprovals,
            "current_user_vote": current_user_vote,
            "can_vote": can_vote,
            "can_write": can_write,
            "has_fallback_content": has_fallback_content,
            "details_loaded": details_loaded,
            "post_url": post_url,
            "note_summary_url": note_summary_url,
            "note_detail_url": note_detail_url,
            "next_url": resolved_next_url,
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
    return render_membership_request_notes(
        context,
        membership_request,
        compact=compact,
        next_url=next_url,
    )
