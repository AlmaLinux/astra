import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
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
from core.membership_notes import add_note
from core.membership_request_workflow import (
    approve_membership_request,
    approve_on_hold_membership_request,
    ignore_membership_request,
    put_membership_request_on_hold,
    reject_membership_request,
    reopen_ignored_membership_request,
)
from core.models import MembershipLog, MembershipRequest
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_VIEW_MEMBERSHIP,
    has_any_membership_manage_permission,
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
        representative = FreeIPAUser.get(representative_username)
        if representative is not None and representative.email:
            return ("users", representative_username)

    org_email = org.primary_contact_email()
    if org_email:
        return ("manual", org_email)

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


def _resolve_requested_by(username: str) -> tuple[str, bool]:
    """Return ``(full_name, is_deleted)`` for a username."""
    if not username:
        return "", False
    user = FreeIPAUser.get(username)
    if user is None:
        return "", True
    return user.full_name, False


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_requests(request: HttpRequest) -> HttpResponse:
    def _build_rows(reqs: list[MembershipRequest]) -> tuple[list[MembershipRequest], list[dict[str, object]]]:
        rows: list[dict[str, object]] = []
        visible: list[MembershipRequest] = []
        for r in reqs:
            requested_log = r.requested_logs[0] if r.requested_logs else None
            requested_by_username = requested_log.actor_username if requested_log is not None else ""
            requested_by_full_name, requested_by_deleted = _resolve_requested_by(requested_by_username)

            if r.is_organization_target:
                org = r.requested_organization
                if org is None:
                    # If the org is gone, the committee can't take action on it.
                    continue

                visible.append(r)
                rows.append(
                    {
                        "r": r,
                        "organization": org,
                        "requested_by_username": requested_by_username,
                        "requested_by_full_name": requested_by_full_name,
                        "requested_by_deleted": requested_by_deleted,
                    }
                )
            else:
                fu = FreeIPAUser.get(r.requested_username)
                if fu is None:
                    # If the user is gone, the committee can't take action on them.
                    continue

                visible.append(r)
                rows.append(
                    {
                        "r": r,
                        "full_name": fu.full_name,
                        "requested_by_username": requested_by_username,
                        "requested_by_full_name": requested_by_full_name,
                        "requested_by_deleted": requested_by_deleted,
                    }
                )
        return visible, rows

    base = MembershipRequest.objects.select_related("membership_type", "requested_organization").prefetch_related(
        Prefetch(
            "logs",
            queryset=MembershipLog.objects.filter(action=MembershipLog.Action.requested)
            .only("actor_username", "membership_request_id", "created_at")
            .order_by("created_at", "pk"),
            to_attr="requested_logs",
        )
    )

    pending_requests_all = list(base.filter(status=MembershipRequest.Status.pending).order_by("requested_at"))
    on_hold_requests_all = list(base.filter(status=MembershipRequest.Status.on_hold).order_by("on_hold_at", "requested_at"))

    pending_requests, pending_rows = _build_rows(pending_requests_all)
    on_hold_requests, on_hold_rows = _build_rows(on_hold_requests_all)

    return render(
        request,
        "core/membership_requests.html",
        {
            "pending_requests": pending_requests,
            "pending_request_rows": pending_rows,
            "on_hold_requests": on_hold_requests,
            "on_hold_request_rows": on_hold_rows,
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(MembershipRequest.objects.select_related("membership_type", "requested_organization"), pk=pk)
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

    return render(
        request,
        "core/membership_request_detail.html",
        {
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
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_request_note_add(request: HttpRequest, pk: int) -> HttpResponse:
    can_vote = has_any_membership_manage_permission(request.user)

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )

    redirect_to = _resolve_post_redirect(request, default=reverse("membership-request-detail", args=[req.pk]))

    actor_username = get_username(request)
    note_action = _normalize_str(request.POST.get("note_action")).lower()
    message = str(request.POST.get("message") or "")

    is_ajax = str(request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"

    try:
        user_message = ""
        if note_action == "vote_approve":
            if not can_vote:
                raise PermissionDenied
            add_note(
                membership_request=req,
                username=actor_username,
                content=message,
                action={"type": "vote", "value": "approve"},
            )
            user_message = "Recorded approve vote."
        elif note_action == "vote_disapprove":
            if not can_vote:
                raise PermissionDenied
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

        if is_ajax:
            from core.templatetags.core_membership_notes import membership_notes

            html = membership_notes(
                {"request": request, **membership_review_permissions(request.user)},
                req,
                compact=False,
                next_url=redirect_to,
            )
            return JsonResponse({"ok": True, "html": str(html), "message": user_message})

        messages.success(request, user_message)
        return redirect(redirect_to)
    except PermissionDenied:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
        raise
    except Exception:
        logger.exception("Failed to add membership note request_pk=%s actor=%s", req.pk, actor_username)
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Failed to add note."}, status=500)

        messages.error(request, "Failed to add note.")
        return redirect(redirect_to)


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_notes_aggregate_note_add(request: HttpRequest) -> HttpResponse:
    redirect_to = _resolve_post_redirect(request, default=reverse("users"))

    actor_username = get_username(request)
    note_action = _normalize_str(request.POST.get("note_action")).lower()
    message = str(request.POST.get("message") or "")
    compact = _normalize_str(request.POST.get("compact")) in {"1", "true", "yes"}

    is_ajax = str(request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"

    if note_action not in {"", "message"}:
        raise PermissionDenied

    target_type = _normalize_str(request.POST.get("aggregate_target_type")).lower()
    target = _normalize_str(request.POST.get("aggregate_target"))
    if not target_type or not target:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Missing target."}, status=400)
        messages.error(request, "Missing target.")
        return redirect(redirect_to)

    try:
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
            org_id = int(target)
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
            if is_ajax:
                return JsonResponse({"ok": False, "error": "Invalid target type."}, status=400)
            messages.error(request, "Invalid target type.")
            return redirect(redirect_to)

        if latest is None:
            if is_ajax:
                return JsonResponse({"ok": False, "error": "No matching membership request."}, status=404)
            messages.error(request, "No matching membership request.")
            return redirect(redirect_to)

        add_note(
            membership_request=latest,
            username=actor_username,
            content=message,
            action=None,
        )

        if is_ajax:
            from core.templatetags.core_membership_notes import (
                membership_notes_aggregate_for_organization,
                membership_notes_aggregate_for_user,
            )

            tag_context = {"request": request, "membership_can_view": True}
            if target_type == "user":
                html = membership_notes_aggregate_for_user(
                    tag_context,
                    target,
                    compact=compact,
                    next_url=redirect_to,
                )
            else:
                html = membership_notes_aggregate_for_organization(
                    tag_context,
                    int(target),
                    compact=compact,
                    next_url=redirect_to,
                )

            return JsonResponse({"ok": True, "html": str(html), "message": "Note added."})

        messages.success(request, "Note added.")
        return redirect(redirect_to)
    except PermissionDenied:
        raise
    except Exception:
        logger.exception(
            "Failed to add aggregate membership note target_type=%s target=%s actor=%s",
            target_type,
            target,
            actor_username,
        )
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Failed to add note."}, status=500)
        messages.error(request, "Failed to add note.")
        return redirect(redirect_to)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_requests_bulk(request: HttpRequest) -> HttpResponse:
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
        return redirect("membership-requests")

    if action not in allowed_actions:
        if bulk_scope == "on_hold":
            messages.error(request, "Choose a valid bulk action for on-hold requests.")
        else:
            messages.error(request, "Choose a valid bulk action.")
        return redirect("membership-requests")

    actor_username = get_username(request)
    reqs_all = list(
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk__in=selected_ids)
        .order_by("pk")
    )
    if not reqs_all:
        messages.error(request, "No matching requests were found.")
        return redirect("membership-requests")

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
            return redirect("membership-requests")
        if bulk_scope == "on_hold":
            messages.error(request, "No matching on-hold requests were found.")
        else:
            messages.error(request, "No matching pending requests were found.")
        return redirect("membership-requests")

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
                logger.exception("Bulk approve failed for membership request pk=%s", req.pk)
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
                logger.exception("Bulk reject failed for membership request pk=%s", req.pk)
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
                logger.exception("Bulk ignore failed for membership request pk=%s", req.pk)
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

    return redirect("membership-requests")


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
            logger.exception("Failed to approve membership request pk=%s", req.pk)
            messages.error(request, "Failed to approve the request.")
            return redirect(redirect_to)

        target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")

        template_name = settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME
        if membership_type.acceptance_template_id is not None:
            template_name = membership_type.acceptance_template.name

        messages.success(request, f"Approved request for {target_label}.")

        approve_extras: dict[str, str] = {}
        if req.is_user_target:
            approve_extras["group_cn"] = membership_type.group_cn

        return _maybe_custom_email_redirect(
            request=request,
            membership_request=req,
            custom_email=custom_email,
            template_name=template_name,
            extra_context=approve_extras,
            redirect_to=redirect_to,
            action_status="approved",
        ) or redirect(redirect_to)

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

        application_url = request.build_absolute_uri(reverse("membership-request-self", args=[req.pk]))

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
        logger.exception("Failed to approve on-hold membership request pk=%s", req.pk)
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
    except Exception:
        logger.exception("Failed to reopen membership request pk=%s", req.pk)
        messages.error(request, "Failed to reopen the request.")
        return redirect(redirect_to)

    target_label = req.requested_username if req.is_user_target else (req.organization_display_name or "organization")
    messages.success(request, f"Reopened request for {target_label}.")
    return redirect(redirect_to)


__all__ = [
    "membership_notes_aggregate_note_add",
    "membership_request_approve",
    "membership_request_approve_on_hold",
    "membership_request_detail",
    "membership_request_ignore",
    "membership_request_note_add",
    "membership_request_reject",
    "membership_request_reopen",
    "membership_request_rfi",
    "membership_requests",
    "membership_requests_bulk",
    "run_membership_request_action",
]
