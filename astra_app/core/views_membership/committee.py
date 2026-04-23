import logging
from collections.abc import Mapping

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy

from core.country_codes import embargoed_country_match_from_user_data
from core.email_context import (
    freeform_message_email_context,
    membership_committee_email_context,
    organization_sponsor_email_context,
)
from core.forms_membership import MembershipRejectForm
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.membership import visible_committee_membership_requests
from core.membership_notes import add_note
from core.membership_notes_preload import build_notes_by_membership_request_id
from core.membership_notifications import organization_sponsor_notification_recipient_email
from core.membership_request_workflow import (
    _resolve_approval_template_name,
    approve_membership_request,
    approve_on_hold_membership_request,
    ignore_membership_request,
    previous_expires_at_for_extension,
    put_membership_request_on_hold,
    reject_membership_request,
    reopen_ignored_membership_request,
)
from core.membership_requests_datatables import (
    build_datatables_payload,
    build_note_details,
    build_note_summary,
    build_on_hold_membership_request_queue,
    build_pending_membership_request_queue,
)
from core.models import MembershipLog, MembershipRequest, Note
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    has_any_membership_manage_permission,
    json_permission_required,
    json_permission_required_any,
    membership_review_permissions,
)
from core.views_utils import (
    _normalize_str,
    _resolve_post_redirect,
    get_username,
    post_only_404,
    require_post_or_404,
    send_mail_url,
)

logger = logging.getLogger(__name__)

_MEMBERSHIP_REQUEST_ALLOWED_FILTERS: frozenset[str] = frozenset(
    {"all", "renewals", "sponsorships", "individuals", "mirrors"}
)


def _custom_email_recipient_for_request(membership_request: MembershipRequest) -> tuple[str, str] | None:
    """Return (Send Mail type, to) for a membership-request custom email.

    For org requests, prefer the representative when it resolves
    to a FreeIPA user with an email address; otherwise fall back to
    Organization.primary_contact_email().
    """

    if membership_request.is_user_target:
        return ("users", membership_request.requested_username)

    org = membership_request.requested_organization
    if org is None:
        return None

    representative_username = org.representative
    if representative_username:
        try:
            representative = FreeIPAUser.get(representative_username)
        except Exception:
            representative = None
        if representative is not None and representative.email:
            return ("users", representative_username)

    recipient_email, _recipient_warning = organization_sponsor_notification_recipient_email(
        organization=org,
        notification_kind="organization workflow notification",
    )
    if recipient_email:
        return ("manual", recipient_email)

    return None


def _custom_email_redirect(
    *,
    request: HttpRequest,
    membership_request: MembershipRequest,
    template_name: str,
    extra_context: dict[str, str],
    redirect_to: str,
    action_status: str,
) -> HttpResponse:
    recipient = _custom_email_recipient_for_request(membership_request)
    if recipient is None:
        messages.error(request, "No recipient is available for a custom email.")
        return redirect(redirect_to)

    to_type, to = recipient
    merged_context = dict(extra_context)
    merged_context.setdefault("membership_request_id", str(membership_request.pk))
    merged_context.update(membership_committee_email_context())
    return redirect(
        send_mail_url(
            to_type=to_type,
            to=to,
            template_name=template_name,
            extra_context=merged_context,
            action_status=action_status,
            reply_to=settings.MEMBERSHIP_COMMITTEE_EMAIL,
        )
    )


def _maybe_custom_email_redirect(
    *,
    request: HttpRequest,
    membership_request: MembershipRequest,
    custom_email: bool,
    template_name: str,
    extra_context: dict[str, str],
    redirect_to: str,
    action_status: str,
) -> HttpResponse | None:
    """Handle org/user custom-email branching for membership request actions.

    Adds membership_type and organization context automatically.
    Returns an HttpResponse for the custom-email redirect, or None if
    custom_email is False so the caller can redirect normally.
    """
    if not custom_email:
        return None

    membership_type = membership_request.membership_type
    merged: dict[str, str] = {
        "membership_type": membership_type.name,
        "membership_type_code": membership_type.code,
    }

    if membership_request.is_organization_target:
        org = membership_request.requested_organization
        merged["organization_name"] = membership_request.organization_display_name
        if org is not None:
            merged.update(organization_sponsor_email_context(organization=org))

    merged.update(extra_context)

    return _custom_email_redirect(
        request=request,
        membership_request=membership_request,
        template_name=template_name,
        extra_context=merged,
        redirect_to=redirect_to,
        action_status=action_status,
    )


def _load_membership_request_for_action(
    request: HttpRequest,
    pk: int,
    *,
    already_status: str,
    already_label: str,
) -> tuple[MembershipRequest, str] | HttpResponse:
    """Load a membership request for a committee action view.

    Handles the POST-only guard, request loading, redirect resolution,
    and already-actioned idempotency check - repeated across approve,
    reject, rfi, and ignore views.

    Returns (membership_request, redirect_to) on success, or an
    HttpResponse redirect when the request is already in the target state.
    """
    require_post_or_404(request)

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    redirect_to = _resolve_post_redirect(request, default=reverse("membership-requests"), use_referer=True)

    if req.status == already_status:
        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
        messages.info(request, f"Request for {target_label} is already {already_label}.")
        return redirect(redirect_to)

    return req, redirect_to


def _resolve_requested_by(
    username: str,
    *,
    users_by_username: Mapping[str, FreeIPAUser] | None = None,
) -> tuple[str, bool]:
    """Return ``(full_name, is_deleted)`` for a username."""
    normalized_username = _normalize_str(username).lower()
    if not normalized_username:
        return "", False
    if users_by_username is not None:
        user = users_by_username.get(normalized_username)
    else:
        user = FreeIPAUser.get(normalized_username)
    if user is None:
        return "", True
    return user.full_name, False


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_requests(request: HttpRequest) -> HttpResponse:
    next_url = request.get_full_path()
    request_id_sentinel = 123456789

    review_permissions = membership_review_permissions(request.user)

    return render(
        request,
        "core/membership_requests.html",
        {
            "clear_filter_url": reverse("membership-requests"),
            "next_url": next_url,
            "membership_request_id_sentinel": request_id_sentinel,
            "membership_request_detail_template": reverse("membership-request-detail", args=[request_id_sentinel]),
            "membership_requests_bulk_url": reverse("api-membership-requests-bulk"),
            "membership_request_approve_template": reverse("api-membership-request-approve", args=[request_id_sentinel]),
            "membership_request_approve_on_hold_template": reverse("api-membership-request-approve-on-hold", args=[request_id_sentinel]),
            "membership_request_reject_template": reverse("api-membership-request-reject", args=[request_id_sentinel]),
            "membership_request_rfi_template": reverse("api-membership-request-rfi", args=[request_id_sentinel]),
            "membership_request_ignore_template": reverse("api-membership-request-ignore", args=[request_id_sentinel]),
            "membership_request_reopen_template": reverse("api-membership-request-reopen", args=[request_id_sentinel]),
            "membership_request_note_add_template": reverse("api-membership-request-notes-add", args=[request_id_sentinel]),
            "membership_request_note_summary_template": reverse("api-membership-request-notes-summary", args=[request_id_sentinel]),
            "membership_request_note_detail_template": reverse("api-membership-request-notes", args=[request_id_sentinel]),
            "membership_user_profile_template": reverse("user-profile", args=["__username__"]),
            "membership_organization_detail_template": reverse("organization-detail", args=[request_id_sentinel]),
            "membership_requests_can_request_info": request.user.has_perm(ASTRA_ADD_SEND_MAIL),
            "membership_requests_notes_can_view": review_permissions["membership_can_view"],
            "membership_requests_notes_can_write": (
                review_permissions["membership_can_add"]
                or review_permissions["membership_can_change"]
                or review_permissions["membership_can_delete"]
            ),
            "membership_requests_notes_can_vote": (
                review_permissions["membership_can_add"]
                or review_permissions["membership_can_change"]
                or review_permissions["membership_can_delete"]
            ),
            "membership_request_rejected_email_template_name": settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
            "membership_request_rfi_email_template_name": settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
        },
    )


def _membership_requests_datatables_error(message: str, *, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _membership_action_json_error(message: str, *, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def _membership_action_target_label(membership_request: MembershipRequest) -> str:
    return (
        membership_request.requested_username
        if membership_request.is_user_target
        else (membership_request.organization_display_name or "organization")
    )


def _load_membership_request_for_action_api(
    pk: int,
    *,
    already_status: str,
    already_label: str,
) -> MembershipRequest | JsonResponse:
    membership_request = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk=pk)
        .first()
    )
    if membership_request is None:
        return _membership_action_json_error("Membership request not found.", status=404)

    if membership_request.status == already_status:
        return JsonResponse(
            {
                "ok": True,
                "message": f"Request for {_membership_action_target_label(membership_request)} is already {already_label}.",
            }
        )

    return membership_request


def _parse_datatables_request(
    request: HttpRequest,
    *,
    allow_queue_filter: bool,
) -> tuple[int, int, int, str | None]:
    allowed_params = {
        "draw",
        "start",
        "length",
        "search[value]",
        "search[regex]",
        "order[0][column]",
        "order[0][dir]",
        "order[0][name]",
        "columns[0][data]",
        "columns[0][name]",
        "columns[0][searchable]",
        "columns[0][orderable]",
        "columns[0][search][value]",
        "columns[0][search][regex]",
    }
    if allow_queue_filter:
        allowed_params.add("queue_filter")

    for key in request.GET.keys():
        if key == "_":
            cache_buster = _normalize_str(request.GET.get(key))
            if not cache_buster.isdigit():
                raise ValueError("Invalid query parameters.")
            continue
        if key not in allowed_params:
            raise ValueError("Invalid query parameters.")

    try:
        draw = int(str(request.GET.get("draw") or ""))
        start = int(str(request.GET.get("start") or ""))
        length = int(str(request.GET.get("length") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid query parameters.") from exc

    if draw < 0 or start < 0:
        raise ValueError("Invalid query parameters.")
    if length <= 0 or length > 100:
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("search[regex]")).lower() == "true":
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("columns[0][search][regex]")).lower() == "true":
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("columns[0][search][value]")).strip():
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("search[value]")).strip():
        raise ValueError("Invalid query parameters.")

    order_column = _normalize_str(request.GET.get("order[0][column]"))
    column_data = _normalize_str(request.GET.get("columns[0][data]"))
    column_searchable = _normalize_str(request.GET.get("columns[0][searchable]")).lower()
    column_orderable = _normalize_str(request.GET.get("columns[0][orderable]")).lower()
    order_dir = _normalize_str(request.GET.get("order[0][dir]")).lower()

    if (
        order_column != "0"
        or column_data != "request_id"
        or column_searchable != "true"
        or column_orderable != "true"
    ):
        raise ValueError("Invalid query parameters.")
    if order_dir != "asc":
        raise ValueError("Invalid query parameters.")

    queue_filter: str | None = None
    if allow_queue_filter:
        raw_filter = _normalize_str(request.GET.get("queue_filter")).lower() or "all"
        if raw_filter not in _MEMBERSHIP_REQUEST_ALLOWED_FILTERS:
            raise ValueError("Invalid query parameters.")
        queue_filter = raw_filter

    return draw, start, length, queue_filter


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_requests_pending_api(request: HttpRequest) -> JsonResponse:
    try:
        draw, start, length, queue_filter = _parse_datatables_request(
            request,
            allow_queue_filter=True,
        )
    except ValueError as exc:
        return _membership_requests_datatables_error(str(exc), status=400)

    selected_filter = queue_filter or "all"
    snapshot = build_pending_membership_request_queue(
        selected_filter=selected_filter,
        visible_membership_requests=visible_committee_membership_requests,
        resolve_requested_by_func=_resolve_requested_by,
        lookup_users=FreeIPAUser.find_lightweight_by_usernames,
        include_rows=True,
    )
    pending_rows = list(snapshot["pending_rows"])
    sliced_rows = pending_rows[start : start + length]
    payload = build_datatables_payload(
        rows=sliced_rows,
        records_total=int(snapshot["filter_counts"]["all"]),
        draw=draw,
    )
    payload["recordsFiltered"] = len(pending_rows)
    payload["pending_filter"] = {
        "selected": selected_filter,
        "options": snapshot["filter_options"],
    }
    return JsonResponse(payload)


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_requests_on_hold_api(request: HttpRequest) -> JsonResponse:
    try:
        draw, start, length, _queue_filter = _parse_datatables_request(
            request,
            allow_queue_filter=False,
        )
    except ValueError as exc:
        return _membership_requests_datatables_error(str(exc), status=400)

    snapshot = build_on_hold_membership_request_queue(
        visible_membership_requests=visible_committee_membership_requests,
        resolve_requested_by_func=_resolve_requested_by,
        lookup_users=FreeIPAUser.find_lightweight_by_usernames,
        include_rows=True,
    )
    on_hold_rows = list(snapshot["on_hold_rows"])
    sliced_rows = on_hold_rows[start : start + length]
    payload = build_datatables_payload(
        rows=sliced_rows,
        records_total=len(on_hold_rows),
        draw=draw,
    )
    payload["recordsFiltered"] = len(on_hold_rows)
    return JsonResponse(payload)


def _membership_notes_read_context(
    request: HttpRequest,
    *,
    membership_request_id: int | None = None,
) -> tuple[MembershipRequest | None, list[Note], str, dict[str, bool]] | JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Authentication required."}, status=403)

    review_permissions = membership_review_permissions(request.user)
    if not review_permissions["membership_can_view"]:
        return JsonResponse({"error": "Permission denied."}, status=403)

    current_username = get_username(request, allow_user_fallback=False)
    if membership_request_id is not None:
        membership_request = (
            MembershipRequest.objects.select_related("membership_type", "requested_organization")
            .filter(pk=membership_request_id)
            .first()
        )
        if membership_request is None:
            return JsonResponse({"error": "Membership request not found."}, status=404)

        notes_by_request_id = build_notes_by_membership_request_id([membership_request_id])
        return membership_request, list(notes_by_request_id.get(membership_request_id, [])), current_username, review_permissions

    target_type = _normalize_str(request.GET.get("target_type")).lower()
    target = _normalize_str(request.GET.get("target"))
    if not target_type or not target:
        return _membership_requests_datatables_error("Missing target.", status=400)

    notes_query = Note.objects.order_by("timestamp", "pk")
    if target_type == "user":
        notes_query = notes_query.filter(membership_request__requested_username=target)
    elif target_type == "org":
        try:
            organization_id = int(target)
        except (TypeError, ValueError):
            return _membership_requests_datatables_error("Invalid target.", status=400)
        notes_query = notes_query.filter(membership_request__requested_organization_id=organization_id)
    else:
        return _membership_requests_datatables_error("Invalid target type.", status=400)

    return None, list(notes_query), current_username, review_permissions


def _membership_notes_summary_context(
    request: HttpRequest,
    *,
    membership_request_id: int | None = None,
) -> tuple[list[Note], str, dict[str, bool]] | JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Authentication required."}, status=403)

    review_permissions = membership_review_permissions(request.user)
    if not review_permissions["membership_can_view"]:
        return JsonResponse({"error": "Permission denied."}, status=403)

    current_username = get_username(request, allow_user_fallback=False)
    if membership_request_id is not None:
        if not MembershipRequest.objects.filter(pk=membership_request_id).exists():
            return JsonResponse({"error": "Membership request not found."}, status=404)

        notes = list(
            Note.objects.filter(membership_request_id=membership_request_id)
            .only("pk", "timestamp", "username", "action")
            .order_by("timestamp", "pk")
        )
        return notes, current_username, review_permissions

    target_type = _normalize_str(request.GET.get("target_type")).lower()
    target = _normalize_str(request.GET.get("target"))
    if not target_type or not target:
        return _membership_requests_datatables_error("Missing target.", status=400)

    notes_query = Note.objects.only("pk", "timestamp", "username", "action").order_by("timestamp", "pk")
    if target_type == "user":
        notes_query = notes_query.filter(membership_request__requested_username=target)
    elif target_type == "org":
        try:
            organization_id = int(target)
        except (TypeError, ValueError):
            return _membership_requests_datatables_error("Invalid target.", status=400)
        notes_query = notes_query.filter(membership_request__requested_organization_id=organization_id)
    else:
        return _membership_requests_datatables_error("Invalid target type.", status=400)

    return list(notes_query), current_username, review_permissions


def membership_request_notes_summary_api(request: HttpRequest, pk: int) -> JsonResponse:
    note_context = _membership_notes_summary_context(request, membership_request_id=pk)
    if isinstance(note_context, JsonResponse):
        return note_context

    notes, current_username, review_permissions = note_context
    summary = build_note_summary(
        notes=notes,
        current_username=current_username,
        review_permissions=review_permissions,
    )
    if summary is None:
        return _membership_requests_datatables_error("Permission denied.", status=403)
    return JsonResponse(summary)


def membership_request_notes_api(request: HttpRequest, pk: int) -> JsonResponse:
    note_context = _membership_notes_read_context(request, membership_request_id=pk)
    if isinstance(note_context, JsonResponse):
        return note_context

    membership_request, notes, current_username, review_permissions = note_context
    assert membership_request is not None
    detail = build_note_details(
        membership_request=membership_request,
        notes=notes,
        current_username=current_username,
        review_permissions=review_permissions,
    )
    if detail is None:
        return _membership_requests_datatables_error("Permission denied.", status=403)
    return JsonResponse(detail)


def membership_notes_aggregate_summary_api(request: HttpRequest) -> JsonResponse:
    note_context = _membership_notes_summary_context(request)
    if isinstance(note_context, JsonResponse):
        return note_context

    notes, current_username, review_permissions = note_context
    summary = build_note_summary(
        notes=notes,
        current_username=current_username,
        review_permissions=review_permissions,
    )
    if summary is None:
        return _membership_requests_datatables_error("Permission denied.", status=403)
    return JsonResponse(summary)


def membership_notes_aggregate_api(request: HttpRequest) -> JsonResponse:
    note_context = _membership_notes_read_context(request)
    if isinstance(note_context, JsonResponse):
        return note_context

    _membership_request, notes, current_username, review_permissions = note_context
    detail = build_note_details(
        notes=notes,
        current_username=current_username,
        review_permissions=review_permissions,
    )
    if detail is None:
        return _membership_requests_datatables_error("Permission denied.", status=403)
    return JsonResponse(detail)


@json_permission_required_any({ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP})
def membership_request_notes_add_api(request: HttpRequest, pk: int) -> JsonResponse:
    """Add a note to a membership request. Returns JSON-only response."""
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )

    actor_username = get_username(request)
    note_action = _normalize_str(request.POST.get("note_action")).lower()
    message = str(request.POST.get("message") or "")

    try:
        if note_action == "vote_approve":
            add_note(
                membership_request=req,
                username=actor_username,
                content=message,
                action={"type": "vote", "value": "approve"},
            )
            user_message = "Recorded approve vote."
        elif note_action == "vote_disapprove":
            add_note(
                membership_request=req,
                username=actor_username,
                content=message,
                action={"type": "vote", "value": "disapprove"},
            )
            user_message = "Recorded disapprove vote."
        else:
            add_note(
                membership_request=req,
                username=actor_username,
                content=message,
                action=None,
            )
            user_message = "Note added."

        return JsonResponse({"ok": True, "message": user_message})
    except Exception:
        logger.exception(
            "Failed to add membership note request_pk=%s actor=%s",
            req.pk,
            actor_username,
            extra=current_exception_log_fields(),
        )
        return JsonResponse({"ok": False, "error": "Failed to add note."}, status=500)


@json_permission_required_any({ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP})
def membership_notes_aggregate_add_api(request: HttpRequest) -> JsonResponse:
    """Add an aggregate note for a user or organization. Returns JSON-only response."""
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    actor_username = get_username(request)
    note_action = _normalize_str(request.POST.get("note_action")).lower()
    message = str(request.POST.get("message") or "")
    target_type = _normalize_str(request.POST.get("target_type")).lower()
    target = _normalize_str(request.POST.get("target"))

    if not target_type or not target:
        return _membership_requests_datatables_error("Missing target.", status=400)

    try:
        if note_action not in {"", "message"}:
            return _membership_requests_datatables_error("Invalid note action.", status=400)

        latest: MembershipRequest | None
        if target_type == "user":
            latest = (
                MembershipRequest.objects.filter(requested_username=target)
                .filter(status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold])
                .order_by("-requested_at", "-pk")
                .first()
            )
            if latest is None:
                latest = MembershipRequest.objects.filter(requested_username=target).order_by(
                    "-requested_at", "-pk"
                ).first()
        elif target_type == "org":
            try:
                org_id = int(target)
            except (TypeError, ValueError):
                return _membership_requests_datatables_error("Invalid target.", status=400)
            latest = (
                MembershipRequest.objects.filter(requested_organization_id=org_id)
                .filter(status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold])
                .order_by("-requested_at", "-pk")
                .first()
            )
            if latest is None:
                latest = MembershipRequest.objects.filter(requested_organization_id=org_id).order_by(
                    "-requested_at", "-pk"
                ).first()
        else:
            return _membership_requests_datatables_error("Invalid target type.", status=400)

        if latest is None:
            return _membership_requests_datatables_error("No matching membership request.", status=404)

        add_note(
            membership_request=latest,
            username=actor_username,
            content=message,
            action=None,
        )

        return JsonResponse({"ok": True, "message": "Note added."})
    except Exception:
        logger.exception(
            "Failed to add aggregate note actor=%s target_type=%s target=%s",
            actor_username,
            target_type,
            target,
            extra=current_exception_log_fields(),
        )
        return JsonResponse({"ok": False, "error": "Failed to add note."}, status=500)


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    return render_membership_request_detail_for_committee(request=request, membership_request=req)


def build_membership_request_detail_committee_context(
    *,
    request: HttpRequest,
    membership_request: MembershipRequest,
) -> dict[str, object]:
    req = membership_request
    request_target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")

    show_on_hold_approve = req.status == MembershipRequest.Status.on_hold and request.user.has_perm(ASTRA_ADD_MEMBERSHIP)

    contact_url = ""
    if request.user.has_perm(ASTRA_ADD_SEND_MAIL):
        recipient = _custom_email_recipient_for_request(req)
        if recipient is not None:
            to_type, to = recipient
            contact_url = send_mail_url(
                to_type=to_type,
                to=to,
                template_name="",
                extra_context={
                    "membership_request_id": str(req.pk),
                },
                reply_to=settings.MEMBERSHIP_COMMITTEE_EMAIL,
            )

    target_user = None
    target_full_name = ""
    target_deleted = False
    embargoed_country_code: str | None = None
    embargoed_country_label: str | None = None
    if req.requested_username:
        target_user = FreeIPAUser.get(req.requested_username)
        target_deleted = target_user is None
        if target_user is not None:
            target_full_name = target_user.full_name
            embargoed_match = embargoed_country_match_from_user_data(user_data=target_user._user_data)
            if embargoed_match is not None:
                embargoed_country_code = embargoed_match.code
                embargoed_country_label = embargoed_match.label
    else:
        org = req.requested_organization
        representative_username = str(org.representative or "").strip() if org is not None else ""
        if representative_username:
            representative_user = FreeIPAUser.get(representative_username)
            if representative_user is not None:
                embargoed_match = embargoed_country_match_from_user_data(
                    user_data=representative_user._user_data,
                )
                if embargoed_match is not None:
                    embargoed_country_code = embargoed_match.code
                    embargoed_country_label = embargoed_match.label

    requested_log = (
        req.logs.filter(action=MembershipLog.Action.requested)
        .only("actor_username", "created_at")
        .order_by("created_at", "pk")
        .first()
    )
    requested_by_username = requested_log.actor_username if requested_log is not None else ""
    requested_by_full_name, requested_by_deleted = _resolve_requested_by(requested_by_username)

    review_permissions = membership_review_permissions(request.user)
    can_write = has_any_membership_manage_permission(request.user)
    can_vote = review_permissions.get("membership_can_add", False) or review_permissions.get("membership_can_change", False) or review_permissions.get("membership_can_delete", False)

    return {
        "req": req,
        "target_user": target_user,
        "target_full_name": target_full_name,
        "target_deleted": target_deleted,
        "embargoed_country_code": embargoed_country_code,
        "embargoed_country_label": embargoed_country_label,
        "requested_by_username": requested_by_username,
        "requested_by_full_name": requested_by_full_name,
        "requested_by_deleted": requested_by_deleted,
        "contact_url": contact_url,
        "show_on_hold_approve": show_on_hold_approve,
        "membership_request_target_label": request_target_label,
        "membership_request_api_approve_url": reverse("api-membership-request-approve", args=[req.pk]),
        "membership_request_api_approve_on_hold_url": reverse("api-membership-request-approve-on-hold", args=[req.pk]),
        "membership_request_api_reject_url": reverse("api-membership-request-reject", args=[req.pk]),
        "membership_request_api_rfi_url": reverse("api-membership-request-rfi", args=[req.pk]),
        "membership_request_api_ignore_url": reverse("api-membership-request-ignore", args=[req.pk]),
        "membership_request_can_request_info": request.user.has_perm(ASTRA_ADD_SEND_MAIL),
        "membership_request_rejected_email_template_name": settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
        "membership_request_rfi_email_template_name": settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
        "membership_request_notes_summary_url": reverse("api-membership-request-notes-summary", args=[req.pk]),
        "membership_request_notes_detail_url": reverse("api-membership-request-notes", args=[req.pk]),
        "membership_request_notes_add_url": reverse("api-membership-request-notes-add", args=[req.pk]),
        "membership_can_view": review_permissions.get("membership_can_view", False),
        "membership_can_write": can_write,
        "membership_can_vote": can_vote,
    }


def render_membership_request_detail_for_committee(
    *,
    request: HttpRequest,
    membership_request: MembershipRequest,
) -> HttpResponse:
    return render(
        request,
        "core/membership_request_detail.html",
        build_membership_request_detail_committee_context(request=request, membership_request=membership_request),
    )


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_requests_bulk(request: HttpRequest) -> HttpResponse:
    redirect_to = _resolve_post_redirect(
        request,
        default=reverse("membership-requests"),
        use_referer=True,
    )
    bulk_scope = _normalize_str(request.POST.get("bulk_scope")).lower() or "pending"

    allowed_statuses: set[str]
    allowed_actions: set[str]
    if bulk_scope == "on_hold":
        allowed_statuses = {MembershipRequest.Status.on_hold}
        allowed_actions = {"reject", "ignore"}
    else:
        # Default behavior matches the existing pending-requests bulk UI.
        bulk_scope = "pending"
        allowed_statuses = {MembershipRequest.Status.pending}
        allowed_actions = {"approve", "reject", "ignore"}

    raw_action = _normalize_str(request.POST.get("bulk_action"))
    action = raw_action
    if action == "accept":
        action = "approve"

    selected_raw = request.POST.getlist("selected")
    selected_ids: list[int] = []
    for v in selected_raw:
        try:
            selected_ids.append(int(v))
        except (TypeError, ValueError):
            continue

    if not selected_ids:
        messages.error(request, "Select one or more requests first.")
        return redirect(redirect_to)

    if action not in allowed_actions:
        if bulk_scope == "on_hold":
            messages.error(request, "Choose a valid bulk action for on-hold requests.")
        else:
            messages.error(request, "Choose a valid bulk action.")
        return redirect(redirect_to)

    actor_username = get_username(request)
    reqs_all = list(
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk__in=selected_ids)
        .order_by("pk")
    )
    if not reqs_all:
        messages.error(request, "No matching requests were found.")
        return redirect(redirect_to)

    target_status = None
    if action == "approve":
        target_status = MembershipRequest.Status.approved
    elif action == "reject":
        target_status = MembershipRequest.Status.rejected
    elif action == "ignore":
        target_status = MembershipRequest.Status.ignored

    already_in_target = []
    if target_status is not None:
        already_in_target = [req for req in reqs_all if req.status == target_status]

    reqs = [req for req in reqs_all if req.status in allowed_statuses]
    if not reqs:
        if already_in_target:
            status_label = str(target_status).replace("_", " ")
            messages.info(request, f"Selected request(s) already {status_label}.")
            return redirect(redirect_to)
        if bulk_scope == "on_hold":
            messages.error(request, "No matching on-hold requests were found.")
        else:
            messages.error(request, "No matching pending requests were found.")
        return redirect(redirect_to)

    approved = 0
    rejected = 0
    ignored = 0
    failures = 0

    for req in reqs:
        if action == "approve":
            try:
                approve_membership_request(
                    membership_request=req,
                    actor_username=actor_username,
                    send_approved_email=True,
                )
            except Exception:
                logger.exception(
                    "Bulk approve failed for membership request pk=%s",
                    req.pk,
                    extra=current_exception_log_fields(),
                )
                failures += 1
                continue

            approved += 1

        elif action == "reject":
            try:
                _, email_error = reject_membership_request(
                    membership_request=req,
                    actor_username=actor_username,
                    rejection_reason="",
                    send_rejected_email=True,
                )
                if email_error is not None:
                    failures += 1
            except Exception:
                logger.exception(
                    "Bulk reject failed for membership request pk=%s",
                    req.pk,
                    extra=current_exception_log_fields(),
                )
                failures += 1
                continue

            rejected += 1

        else:
            try:
                ignore_membership_request(
                    membership_request=req,
                    actor_username=actor_username,
                )
            except Exception:
                logger.exception(
                    "Bulk ignore failed for membership request pk=%s",
                    req.pk,
                    extra=current_exception_log_fields(),
                )
                failures += 1
                continue

            ignored += 1

    if approved:
        messages.success(request, f"Approved {approved} request(s).")
    if rejected:
        messages.success(request, f"Rejected {rejected} request(s).")
    if ignored:
        messages.success(request, f"Ignored {ignored} request(s).")
    if failures:
        messages.error(request, f"Failed to process {failures} request(s).")
    if already_in_target:
        status_label = str(target_status).replace("_", " ") if target_status is not None else "processed"
        messages.info(request, f"Selected request(s) already {status_label}.")

    return redirect(redirect_to)


def run_membership_request_action(request: HttpRequest, pk: int, *, action: str) -> HttpResponse:
    if action == "approve":
        result = _load_membership_request_for_action(
            request,
            pk,
            already_status=MembershipRequest.Status.approved,
            already_label="approved",
        )
        if isinstance(result, HttpResponse):
            return result

        req, redirect_to = result
        membership_type = req.membership_type
        custom_email = bool(str(request.POST.get("custom_email") or "").strip())
        previous_expires_at = previous_expires_at_for_extension(
            membership_request=req,
            membership_type=membership_type,
        )

        try:
            approve_membership_request(
                membership_request=req,
                actor_username=get_username(request),
                send_approved_email=not custom_email,
                approved_email_template_name=None,
            )
        except ValidationError as exc:
            message = exc.messages[0] if exc.messages else str(exc)
            messages.error(request, message)
            return redirect(redirect_to)
        except Exception:
            logger.exception(
                "Failed to approve membership request pk=%s",
                req.pk,
                extra=current_exception_log_fields(),
            )
            messages.error(request, "Failed to approve the request.")
            return redirect(redirect_to)

        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")

        template_name = _resolve_approval_template_name(
            membership_type=membership_type,
            override=None,
            previous_expires_at=previous_expires_at,
        )

        approve_extras: dict[str, str] = {}
        if req.is_user_target:
            approve_extras["group_cn"] = membership_type.group_cn

        custom_email_redirect = _maybe_custom_email_redirect(
            request=request,
            membership_request=req,
            custom_email=custom_email,
            template_name=template_name,
            extra_context=approve_extras,
            redirect_to=redirect_to,
            action_status="approved",
        )
        if custom_email_redirect is not None:
            return custom_email_redirect

        messages.success(request, f"Approved request for {target_label}.")
        return redirect(redirect_to)

    if action == "reject":
        result = _load_membership_request_for_action(
            request,
            pk,
            already_status=MembershipRequest.Status.rejected,
            already_label="rejected",
        )
        if isinstance(result, HttpResponse):
            return result

        req, redirect_to = result
        custom_email = bool(str(request.POST.get("custom_email") or "").strip())

        form = MembershipRejectForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Invalid rejection reason.")
            return redirect(redirect_to)

        reason = str(form.cleaned_data.get("reason") or "").strip()

        _, email_error = reject_membership_request(
            membership_request=req,
            actor_username=get_username(request),
            rejection_reason=reason,
            send_rejected_email=not custom_email,
        )

        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
        messages.success(request, f"Rejected request for {target_label}.")

        if email_error is not None:
            messages.error(request, "Request was rejected, but the email could not be sent.")

        return _maybe_custom_email_redirect(
            request=request,
            membership_request=req,
            custom_email=custom_email,
            template_name=settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
            extra_context=freeform_message_email_context(key="rejection_reason", value=reason),
            redirect_to=redirect_to,
            action_status="rejected",
        ) or redirect(redirect_to)

    if action == "rfi":
        result = _load_membership_request_for_action(
            request,
            pk,
            already_status=MembershipRequest.Status.on_hold,
            already_label="on hold",
        )
        if isinstance(result, HttpResponse):
            return result

        req, redirect_to = result
        custom_email = bool(str(request.POST.get("custom_email") or "").strip())
        rfi_message = str(request.POST.get("rfi_message") or "").strip()

        application_url = request.build_absolute_uri(reverse("membership-request-detail", args=[req.pk]))

        _log, email_error = put_membership_request_on_hold(
            membership_request=req,
            actor_username=get_username(request),
            rfi_message=rfi_message,
            send_rfi_email=not custom_email,
            application_url=application_url,
        )

        rfi_extras = {
            "rfi_message": rfi_message,
            "application_url": application_url,
            **freeform_message_email_context(key="rfi_message", value=rfi_message),
        }
        email_redirect = _maybe_custom_email_redirect(
            request=request,
            membership_request=req,
            custom_email=custom_email,
            template_name=settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
            extra_context=rfi_extras,
            redirect_to=redirect_to,
            action_status="on_hold",
        )
        if email_redirect is not None:
            return email_redirect

        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
        messages.success(request, f"Sent Request for Information for {target_label}.")
        if email_error is not None:
            messages.error(request, "Request was put on hold, but the email could not be sent.")
        return redirect(redirect_to)

    if action == "ignore":
        result = _load_membership_request_for_action(
            request,
            pk,
            already_status=MembershipRequest.Status.ignored,
            already_label="ignored",
        )
        if isinstance(result, HttpResponse):
            return result

        req, redirect_to = result
        ignore_membership_request(
            membership_request=req,
            actor_username=get_username(request),
        )

        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
        messages.success(request, f"Ignored request for {target_label}.")
        return redirect(redirect_to)

    raise Http404("Not found")


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_request_approve_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req_or_error = _load_membership_request_for_action_api(
        pk,
        already_status=MembershipRequest.Status.approved,
        already_label="approved",
    )
    if isinstance(req_or_error, JsonResponse):
        return req_or_error

    req = req_or_error
    try:
        approve_membership_request(
            membership_request=req,
            actor_username=get_username(request),
            send_approved_email=True,
            approved_email_template_name=None,
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        return _membership_action_json_error(message, status=400)
    except Exception:
        logger.exception(
            "Failed to approve membership request pk=%s",
            req.pk,
            extra=current_exception_log_fields(),
        )
        return _membership_action_json_error("Failed to approve the request.", status=500)

    return JsonResponse({"ok": True, "message": f"Approved request for {_membership_action_target_label(req)}."})


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_request_approve_on_hold_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk=pk)
        .first()
    )
    if req is None:
        return _membership_action_json_error("Membership request not found.", status=404)

    if req.status == MembershipRequest.Status.approved:
        return JsonResponse({"ok": True, "message": f"Request for {_membership_action_target_label(req)} is already approved."})

    justification = str(request.POST.get("justification") or "").strip()
    if not justification:
        return _membership_action_json_error("Override justification is required.", status=400)

    try:
        approve_on_hold_membership_request(
            request_id=req.pk,
            actor_username=get_username(request),
            justification=justification,
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        return _membership_action_json_error(message, status=400)
    except Exception:
        logger.exception(
            "Failed to approve on-hold membership request pk=%s",
            req.pk,
            extra=current_exception_log_fields(),
        )
        return _membership_action_json_error("Failed to approve the on-hold request.", status=500)

    return JsonResponse({"ok": True, "message": f"Approved on-hold request for {_membership_action_target_label(req)}."})


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_request_reject_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req_or_error = _load_membership_request_for_action_api(
        pk,
        already_status=MembershipRequest.Status.rejected,
        already_label="rejected",
    )
    if isinstance(req_or_error, JsonResponse):
        return req_or_error

    req = req_or_error
    form = MembershipRejectForm(request.POST)
    if not form.is_valid():
        return _membership_action_json_error("Invalid rejection reason.", status=400)

    reason = str(form.cleaned_data.get("reason") or "").strip()
    _log, email_error = reject_membership_request(
        membership_request=req,
        actor_username=get_username(request),
        rejection_reason=reason,
        send_rejected_email=True,
    )

    payload: dict[str, str | bool] = {
        "ok": True,
        "message": f"Rejected request for {_membership_action_target_label(req)}.",
    }
    if email_error is not None:
        payload["warning"] = "Request was rejected, but the email could not be sent."
    return JsonResponse(payload)


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_request_rfi_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req_or_error = _load_membership_request_for_action_api(
        pk,
        already_status=MembershipRequest.Status.on_hold,
        already_label="on hold",
    )
    if isinstance(req_or_error, JsonResponse):
        return req_or_error

    req = req_or_error
    rfi_message = str(request.POST.get("rfi_message") or "").strip()
    application_url = request.build_absolute_uri(reverse("membership-request-detail", args=[req.pk]))

    _log, email_error = put_membership_request_on_hold(
        membership_request=req,
        actor_username=get_username(request),
        rfi_message=rfi_message,
        send_rfi_email=True,
        application_url=application_url,
    )

    payload: dict[str, str | bool] = {
        "ok": True,
        "message": f"Sent Request for Information for {_membership_action_target_label(req)}.",
    }
    if email_error is not None:
        payload["warning"] = "Request was put on hold, but the email could not be sent."
    return JsonResponse(payload)


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_request_ignore_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req_or_error = _load_membership_request_for_action_api(
        pk,
        already_status=MembershipRequest.Status.ignored,
        already_label="ignored",
    )
    if isinstance(req_or_error, JsonResponse):
        return req_or_error

    req = req_or_error
    ignore_membership_request(
        membership_request=req,
        actor_username=get_username(request),
    )

    return JsonResponse({"ok": True, "message": f"Ignored request for {_membership_action_target_label(req)}."})


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_request_reopen_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    req = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk=pk)
        .first()
    )
    if req is None:
        return _membership_action_json_error("Membership request not found.", status=404)

    try:
        reopen_ignored_membership_request(
            membership_request=req,
            actor_username=get_username(request),
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        return _membership_action_json_error(message, status=400)
    except IntegrityError:
        return _membership_action_json_error(
            "Cannot reopen: another open request for this target already exists.",
            status=400,
        )
    except Exception:
        logger.exception(
            "Failed to reopen membership request pk=%s",
            req.pk,
            extra=current_exception_log_fields(),
        )
        return _membership_action_json_error("Failed to reopen the request.", status=500)

    return JsonResponse({"ok": True, "message": f"Reopened request for {_membership_action_target_label(req)}."})


@json_permission_required(ASTRA_ADD_MEMBERSHIP)
def membership_requests_bulk_api(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _membership_requests_datatables_error("Method not allowed.", status=405)

    bulk_scope = _normalize_str(request.POST.get("bulk_scope")).lower() or "pending"

    allowed_statuses: set[str]
    allowed_actions: set[str]
    if bulk_scope == "on_hold":
        allowed_statuses = {MembershipRequest.Status.on_hold}
        allowed_actions = {"reject", "ignore"}
    else:
        bulk_scope = "pending"
        allowed_statuses = {MembershipRequest.Status.pending}
        allowed_actions = {"approve", "reject", "ignore"}

    raw_action = _normalize_str(request.POST.get("bulk_action"))
    action = "approve" if raw_action == "accept" else raw_action

    selected_raw = request.POST.getlist("selected")
    selected_ids: list[int] = []
    for value in selected_raw:
        try:
            selected_ids.append(int(value))
        except (TypeError, ValueError):
            continue

    if not selected_ids:
        return _membership_action_json_error("Select one or more requests first.", status=400)

    if action not in allowed_actions:
        if bulk_scope == "on_hold":
            return _membership_action_json_error("Choose a valid bulk action for on-hold requests.", status=400)
        return _membership_action_json_error("Choose a valid bulk action.", status=400)

    actor_username = get_username(request)
    reqs_all = list(
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk__in=selected_ids)
        .order_by("pk")
    )
    if not reqs_all:
        return _membership_action_json_error("No matching requests were found.", status=404)

    target_status = None
    if action == "approve":
        target_status = MembershipRequest.Status.approved
    elif action == "reject":
        target_status = MembershipRequest.Status.rejected
    elif action == "ignore":
        target_status = MembershipRequest.Status.ignored

    already_in_target = []
    if target_status is not None:
        already_in_target = [req for req in reqs_all if req.status == target_status]

    reqs = [req for req in reqs_all if req.status in allowed_statuses]
    if not reqs:
        if already_in_target:
            status_label = str(target_status).replace("_", " ")
            return JsonResponse({"ok": True, "message": f"Selected request(s) already {status_label}."})
        if bulk_scope == "on_hold":
            return _membership_action_json_error("No matching on-hold requests were found.", status=400)
        return _membership_action_json_error("No matching pending requests were found.", status=400)

    approved = 0
    rejected = 0
    ignored = 0
    failures = 0

    for req in reqs:
        if action == "approve":
            try:
                approve_membership_request(
                    membership_request=req,
                    actor_username=actor_username,
                    send_approved_email=True,
                )
            except Exception:
                logger.exception(
                    "Bulk approve failed for membership request pk=%s",
                    req.pk,
                    extra=current_exception_log_fields(),
                )
                failures += 1
                continue

            approved += 1

        elif action == "reject":
            try:
                _, email_error = reject_membership_request(
                    membership_request=req,
                    actor_username=actor_username,
                    rejection_reason="",
                    send_rejected_email=True,
                )
                if email_error is not None:
                    failures += 1
            except Exception:
                logger.exception(
                    "Bulk reject failed for membership request pk=%s",
                    req.pk,
                    extra=current_exception_log_fields(),
                )
                failures += 1
                continue

            rejected += 1

        else:
            try:
                ignore_membership_request(
                    membership_request=req,
                    actor_username=actor_username,
                )
            except Exception:
                logger.exception(
                    "Bulk ignore failed for membership request pk=%s",
                    req.pk,
                    extra=current_exception_log_fields(),
                )
                failures += 1
                continue

            ignored += 1

    return JsonResponse(
        {
            "ok": True,
            "approved": approved,
            "rejected": rejected,
            "ignored": ignored,
            "failures": failures,
            "already_in_target": len(already_in_target),
        }
    )


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_approve(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="approve")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_request_approve_on_hold(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    redirect_to = _resolve_post_redirect(request, default=reverse("membership-requests"), use_referer=True)

    if req.status == MembershipRequest.Status.approved:
        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
        messages.info(request, f"Request for {target_label} is already approved.")
        return redirect(redirect_to)

    justification = str(request.POST.get("justification") or "").strip()
    if not justification:
        messages.error(request, "Override justification is required.")
        return redirect(redirect_to)

    try:
        approve_on_hold_membership_request(
            request_id=req.pk,
            actor_username=get_username(request),
            justification=justification,
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        messages.error(request, message)
        return redirect(redirect_to)
    except Exception:
        logger.exception(
            "Failed to approve on-hold membership request pk=%s",
            req.pk,
            extra=current_exception_log_fields(),
        )
        messages.error(request, "Failed to approve the on-hold request.")
        return redirect(redirect_to)

    target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
    messages.success(request, f"Approved on-hold request for {target_label}.")
    return redirect(redirect_to)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_reject(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="reject")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_rfi(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="rfi")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_ignore(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="ignore")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_request_reopen(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    redirect_to = _resolve_post_redirect(request, default=reverse("membership-requests"), use_referer=True)

    try:
        reopen_ignored_membership_request(
            membership_request=req,
            actor_username=get_username(request),
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        messages.error(request, message)
        return redirect(redirect_to)
    except IntegrityError:
        messages.error(request, "Cannot reopen: another open request for this target already exists.")
        return redirect(redirect_to)
    except Exception:
        logger.exception(
            "Failed to reopen membership request pk=%s",
            req.pk,
            extra=current_exception_log_fields(),
        )
        messages.error(request, "Failed to reopen the request.")
        return redirect(redirect_to)

    target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
    messages.success(request, f"Reopened request for {target_label}.")
    return redirect(redirect_to)


__all__ = [
    "membership_request_approve_api",
    "membership_request_approve_on_hold_api",
    "membership_request_approve",
    "membership_request_ignore_api",
    "membership_request_approve_on_hold",
    "membership_request_detail",
    "membership_request_ignore",
    "membership_request_reject_api",
    "membership_request_reject",
    "membership_request_reopen_api",
    "membership_request_reopen",
    "membership_request_rfi_api",
    "membership_request_rfi",
    "membership_requests_bulk_api",
    "membership_requests",
    "membership_requests_bulk",
    "run_membership_request_action",
]
