from __future__ import annotations

import datetime
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core.backends import FreeIPAUser
from core.country_codes import country_code_status_from_user_data, country_label_from_code, embargoed_country_codes_from_settings
from core.email_context import (
    freeform_message_email_context,
    membership_committee_email_context,
    organization_sponsor_email_context,
)
from core.forms_membership import (
    MembershipRejectForm,
    MembershipRequestForm,
    MembershipRequestUpdateResponsesForm,
    MembershipUpdateExpiryForm,
)
from core.membership import get_valid_memberships_for_username
from core.membership_notes import CUSTOS, add_note
from core.membership_request_workflow import (
    approve_membership_request,
    ignore_membership_request,
    put_membership_request_on_hold,
    record_membership_request_created,
    reject_membership_request,
    rescind_membership_request,
    resubmit_membership_request,
)
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    Organization,
    OrganizationSponsorship,
)
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    MEMBERSHIP_PERMISSIONS,
    has_any_membership_permission,
    json_permission_required_any,
)
from core.views_utils import _normalize_str, block_action_without_coc, block_action_without_country_code

logger = logging.getLogger(__name__)


def _membership_request_target_label(membership_request: MembershipRequest) -> str:
    if membership_request.requested_username:
        return membership_request.requested_username

    org = membership_request.requested_organization
    return org.name if org is not None else (membership_request.requested_organization_name or "organization")


def _send_mail_url(
    *,
    to_type: str,
    to: str,
    template_name: str,
    extra_context: dict[str, str],
    action_status: str = "",
    reply_to: str | None = None,
) -> str:
    query_params = {
        "type": to_type,
        "to": to,
        "template": template_name,
        **extra_context,
    }
    reply_to_value = str(reply_to or "").strip()
    if reply_to_value:
        query_params["reply_to"] = reply_to_value
    if action_status:
        query_params["action_status"] = action_status
    send_mail_url = reverse("send-mail")
    return f"{send_mail_url}?{urlencode(query_params)}"


def _custom_email_recipient_for_request(membership_request: MembershipRequest) -> tuple[str, str] | None:
    """Return (Send Mail type, to) for a membership-request custom email.

    For org requests, prefer the representative when it resolves
    to a FreeIPA user with an email address; otherwise fall back to
    Organization.primary_contact_email().
    """

    if membership_request.requested_username:
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
        _send_mail_url(
            to_type=to_type,
            to=to,
            template_name=template_name,
            extra_context=merged_context,
            action_status=action_status,
            reply_to=settings.MEMBERSHIP_COMMITTEE_EMAIL,
        )
    )


def _pagination_context(*, paginator: Paginator, page_obj, page_url_prefix: str) -> dict[str, object]:
    total_pages = paginator.num_pages
    current_page = page_obj.number
    if total_pages <= 10:
        page_numbers = list(range(1, total_pages + 1))
        show_first = False
        show_last = False
    else:
        start = max(1, current_page - 2)
        end = min(total_pages, current_page + 2)
        page_numbers = list(range(start, end + 1))
        show_first = 1 not in page_numbers
        show_last = total_pages not in page_numbers

    return {
        "paginator": paginator,
        "page_obj": page_obj,
        "is_paginated": paginator.num_pages > 1,
        "page_numbers": page_numbers,
        "show_first": show_first,
        "show_last": show_last,
        "page_url_prefix": page_url_prefix,
    }


def membership_request(request: HttpRequest) -> HttpResponse:
    username = request.user.get_username()
    if not username:
        raise Http404("User not found")

    prefill_membership_type = str(request.GET.get("membership_type") or "").strip()

    fu = FreeIPAUser.get(username)
    if fu is None:
        messages.error(request, "Unable to load your FreeIPA profile.")
        return redirect("user-profile", username=username)

    blocked = block_action_without_coc(
        request,
        username=username,
        action_label="request or renew memberships",
    )
    if blocked is not None:
        return blocked

    blocked = block_action_without_country_code(
        request,
        user_data=fu._user_data,
        action_label="request or renew memberships",
    )
    if blocked is not None:
        return blocked

    if request.method == "POST":
        form = MembershipRequestForm(request.POST, username=username)
        if form.is_valid():
            membership_type: MembershipType = form.cleaned_data["membership_type"]
            if not membership_type.enabled:
                form.add_error("membership_type", "That membership type is not available.")
            elif not membership_type.isIndividual and membership_type.code != "mirror":
                form.add_error("membership_type", "That membership type is not available.")
            elif not membership_type.group_cn:
                form.add_error("membership_type", "That membership type is not currently linked to a group.")
            else:
                existing = (
                    MembershipRequest.objects.filter(
                        requested_username=username,
                        membership_type=membership_type,
                        status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                    )
                    .order_by("-requested_at")
                    .first()
                )
                if existing is not None:
                    messages.info(request, "You already have a pending request of that type.")
                    return redirect("user-profile", username=username)

                responses = form.responses()

                mr = MembershipRequest.objects.create(
                    requested_username=username,
                    membership_type=membership_type,
                    status=MembershipRequest.Status.pending,
                    responses=responses,
                )

                record_membership_request_created(
                    membership_request=mr,
                    actor_username=username,
                    send_submitted_email=True,
                )

                try:
                    status = country_code_status_from_user_data(fu._user_data)
                    if status.is_valid and status.code in embargoed_country_codes_from_settings():
                        add_note(
                            membership_request=mr,
                            username=CUSTOS,
                            content=f"This user's declared country, {country_label_from_code(status.code)}, is on the list of embargoed countries.",
                        )
                except Exception:
                    logger.exception(
                        "Failed to record embargoed-country system note request_id=%s username=%s",
                        mr.pk,
                        username,
                    )

                messages.success(request, "Membership request submitted.")
                return redirect("user-profile", username=username)
    else:
        form = MembershipRequestForm(username=username, initial={"membership_type": prefill_membership_type})

    return render(
        request,
        "core/membership_request.html",
        {
            "form": form,
        },
    )


def _user_can_access_membership_request(*, username: str, membership_request: MembershipRequest) -> bool:
    normalized_username = str(username or "").strip().lower()
    if not normalized_username:
        return False

    if membership_request.requested_username:
        return membership_request.requested_username.strip().lower() == normalized_username

    org = membership_request.requested_organization
    if org is None:
        return False

    return str(org.representative or "").strip().lower() == normalized_username


def membership_request_self(request: HttpRequest, pk: int) -> HttpResponse:
    username = request.user.get_username()
    if not username:
        raise Http404("User not found")

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    if not _user_can_access_membership_request(username=username, membership_request=req):
        # Avoid leaking that the request exists.
        raise Http404("Not found")

    fu = FreeIPAUser.get(username)
    user_email = fu.email if fu is not None else ""

    if request.method == "POST":
        if req.status != MembershipRequest.Status.on_hold:
            raise PermissionDenied

        form = MembershipRequestUpdateResponsesForm(request.POST, membership_request=req)
        if not form.is_valid():
            messages.error(request, "Invalid request update.")
            return render(
                request,
                "core/membership_request_self.html",
                {
                    "req": req,
                    "form": form,
                    "user_email": user_email,
                },
            )

        try:
            resubmit_membership_request(
                membership_request=req,
                actor_username=username,
                updated_responses=form.responses(),
            )
        except ValidationError as e:
            msg = e.messages[0] if getattr(e, "messages", None) else str(e)
            form.add_error(None, msg)
            return render(
                request,
                "core/membership_request_self.html",
                {
                    "req": req,
                    "form": form,
                    "user_email": user_email,
                },
            )

        messages.success(request, "Your request has been resubmitted for review.")
        return redirect("membership-request-self", pk=req.pk)

    form: MembershipRequestUpdateResponsesForm | None = None
    if req.status == MembershipRequest.Status.on_hold:
        form = MembershipRequestUpdateResponsesForm(membership_request=req)

    return render(
        request,
        "core/membership_request_self.html",
        {
            "req": req,
            "form": form,
            "user_email": user_email,
        },
    )


def membership_request_rescind(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    username = request.user.get_username()
    if not username:
        raise Http404("User not found")

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    if not _user_can_access_membership_request(username=username, membership_request=req):
        raise Http404("Not found")

    rescind_membership_request(membership_request=req, actor_username=username)
    messages.success(request, "Your request has been rescinded.")
    return redirect("user-profile", username=username)


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    username = _normalize_str(request.GET.get("username"))
    raw_org = _normalize_str(request.GET.get("organization"))
    organization_id = int(raw_org) if raw_org.isdigit() else None
    page_number = _normalize_str(request.GET.get("page")) or None

    logs = MembershipLog.objects.select_related(
        "membership_type",
        "membership_request",
        "membership_request__membership_type",
        "target_organization",
    ).all()
    if username:
        logs = logs.filter(target_username=username)
    if organization_id is not None:
        logs = logs.filter(Q(target_organization_id=organization_id) | Q(target_organization_code=str(organization_id)))
    if q:
        logs = logs.filter(
            Q(target_username__icontains=q)
            | Q(target_organization__name__icontains=q)
            | Q(target_organization_code__icontains=q)
            | Q(target_organization_name__icontains=q)
            | Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    logs = logs.order_by("-created_at")
    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(page_number)
    query_params: dict[str, str] = {}
    if q:
        query_params["q"] = q
    if username:
        query_params["username"] = username
    if organization_id is not None:
        query_params["organization"] = str(organization_id)
    qs = urlencode(query_params)
    page_url_prefix = f"?{qs}&page=" if qs else "?page="

    return render(
        request,
        "core/membership_audit_log.html",
        {
            "logs": page_obj.object_list,
            "filter_username": username,
            "filter_username_param": username,
            "filter_organization": str(organization_id) if organization_id is not None else "",
            "filter_organization_param": str(organization_id) if organization_id is not None else "",
            "q": q,
            **_pagination_context(paginator=paginator, page_obj=page_obj, page_url_prefix=page_url_prefix),
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_organization(request: HttpRequest, organization_id: int) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    page_number = _normalize_str(request.GET.get("page")) or None

    logs = (
        MembershipLog.objects.select_related(
            "membership_type",
            "membership_request",
            "membership_request__membership_type",
            "target_organization",
        )
        .filter(Q(target_organization_id=organization_id) | Q(target_organization_code=str(organization_id)))
        .order_by("-created_at")
    )
    if q:
        logs = logs.filter(
            Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(page_number)
    query_params: dict[str, str] = {}
    if q:
        query_params["q"] = q
    qs = urlencode(query_params)
    page_url_prefix = f"?{qs}&page=" if qs else "?page="

    return render(
        request,
        "core/membership_audit_log.html",
        {
            "logs": page_obj.object_list,
            "filter_username": "",
            "filter_username_param": "",
            "filter_organization": str(organization_id),
            "filter_organization_param": str(organization_id),
            "q": q,
            **_pagination_context(paginator=paginator, page_obj=page_obj, page_url_prefix=page_url_prefix),
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_user(request: HttpRequest, username: str) -> HttpResponse:
    username = _normalize_str(username)
    q = _normalize_str(request.GET.get("q"))
    page_number = _normalize_str(request.GET.get("page")) or None

    logs = (
        MembershipLog.objects.select_related(
            "membership_type",
            "membership_request",
            "membership_request__membership_type",
        )
        .filter(target_username=username)
        .order_by("-created_at")
    )
    if q:
        logs = logs.filter(
            Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(page_number)
    query_params: dict[str, str] = {}
    if q:
        query_params["q"] = q
    qs = urlencode(query_params)
    page_url_prefix = f"?{qs}&page=" if qs else "?page="

    return render(
        request,
        "core/membership_audit_log.html",
        {
            "logs": page_obj.object_list,
            "filter_username": username,
            "filter_username_param": "",
            "q": q,
            **_pagination_context(paginator=paginator, page_obj=page_obj, page_url_prefix=page_url_prefix),
        },
    )


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_requests(request: HttpRequest) -> HttpResponse:
    def _build_rows(reqs: list[MembershipRequest]) -> tuple[list[MembershipRequest], list[dict[str, object]]]:
        rows: list[dict[str, object]] = []
        visible: list[MembershipRequest] = []
        for r in reqs:
            requested_log = r.requested_logs[0] if r.requested_logs else None
            requested_by_username = requested_log.actor_username if requested_log is not None else ""
            requested_by_full_name = ""
            requested_by_deleted = False
            if requested_by_username:
                requested_by_user = FreeIPAUser.get(requested_by_username)
                requested_by_deleted = requested_by_user is None
                if requested_by_user is not None:
                    requested_by_full_name = requested_by_user.full_name

            if r.requested_username == "":
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

    contact_url = ""
    if request.user.has_perm(ASTRA_ADD_SEND_MAIL):
        recipient = _custom_email_recipient_for_request(req)
        if recipient is not None:
            to_type, to = recipient
            contact_url = _send_mail_url(
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
            status = country_code_status_from_user_data(target_user._user_data)
            embargoed_country_codes = embargoed_country_codes_from_settings()
            if status.is_valid and status.code in embargoed_country_codes:
                embargoed_country_code = status.code
                embargoed_country_label = country_label_from_code(status.code)

    requested_log = (
        req.logs.filter(action=MembershipLog.Action.requested)
        .only("actor_username", "created_at")
        .order_by("created_at", "pk")
        .first()
    )
    requested_by_username = requested_log.actor_username if requested_log is not None else ""
    requested_by_full_name = ""
    requested_by_deleted = False
    if requested_by_username:
        requested_by_user = FreeIPAUser.get(requested_by_username)
        requested_by_deleted = requested_by_user is None
        if requested_by_user is not None:
            requested_by_full_name = requested_by_user.full_name

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
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_note_add(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    can_vote = any(
        request.user.has_perm(p)
        for p in (
            ASTRA_ADD_MEMBERSHIP,
            ASTRA_CHANGE_MEMBERSHIP,
            ASTRA_DELETE_MEMBERSHIP,
        )
    )

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        redirect_to = reverse("membership-request-detail", args=[req.pk])

    actor_username = str(request.user.get_username() or "").strip()
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
                {
                    "request": request,
                    "membership_can_add": request.user.has_perm(ASTRA_ADD_MEMBERSHIP),
                    "membership_can_change": request.user.has_perm(ASTRA_CHANGE_MEMBERSHIP),
                    "membership_can_delete": request.user.has_perm(ASTRA_DELETE_MEMBERSHIP),
                },
                req,
                compact=False,
                next_url=redirect_to,
            )
            return JsonResponse({"ok": True, "html": str(html), "message": user_message})

        messages.success(request, user_message)
        return redirect(redirect_to)
    except Exception:
        logger.exception("Failed to add membership note request_pk=%s actor=%s", req.pk, actor_username)
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Failed to add note."}, status=500)

        messages.error(request, "Failed to add note.")
        return redirect(redirect_to)


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_notes_aggregate_note_add(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        redirect_to = reverse("users")

    actor_username = str(request.user.get_username() or "").strip()
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
def membership_requests_bulk(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

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

    actor_username = request.user.get_username()
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


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_approve(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    req = get_object_or_404(MembershipRequest.objects.select_related("membership_type", "requested_organization"), pk=pk)
    membership_type = req.membership_type

    custom_email = bool(str(request.POST.get("custom_email") or "").strip())

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        redirect_to = referer or reverse("membership-requests")
        if redirect_to and not url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            redirect_to = reverse("membership-requests")

    if req.status == MembershipRequest.Status.approved:
        target_label = _membership_request_target_label(req)
        messages.info(request, f"Request for {target_label} is already approved.")
        return redirect(redirect_to)

    try:
        approve_membership_request(
            membership_request=req,
            actor_username=request.user.get_username(),
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

    target_label = _membership_request_target_label(req)

    template_name = settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME
    if membership_type.acceptance_template_id is not None:
        template_name = membership_type.acceptance_template.name

    messages.success(request, f"Approved request for {target_label}.")

    if req.requested_username == "":
        org = req.requested_organization

        if custom_email:
            return _custom_email_redirect(
                request=request,
                membership_request=req,
                template_name=template_name,
                extra_context={
                    "organization_name": org.name if org is not None else (req.requested_organization_name or ""),
                    **(organization_sponsor_email_context(organization=org) if org is not None else {}),
                    "membership_type": membership_type.name,
                    "membership_type_code": membership_type.code,
                },
                redirect_to=redirect_to,
                action_status="approved",
            )
        return redirect(redirect_to)

    if custom_email:
        return _custom_email_redirect(
            request=request,
            membership_request=req,
            template_name=template_name,
            extra_context={
                "membership_type": membership_type.name,
                "membership_type_code": membership_type.code,
                "group_cn": membership_type.group_cn,
            },
            redirect_to=redirect_to,
            action_status="approved",
        )
    return redirect(redirect_to)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_reject(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    req = get_object_or_404(MembershipRequest.objects.select_related("membership_type", "requested_organization"), pk=pk)
    membership_type = req.membership_type

    custom_email = bool(str(request.POST.get("custom_email") or "").strip())

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        redirect_to = referer or reverse("membership-requests")
        if redirect_to and not url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            redirect_to = reverse("membership-requests")

    if req.status == MembershipRequest.Status.rejected:
        target_label = _membership_request_target_label(req)
        messages.info(request, f"Request for {target_label} is already rejected.")
        return redirect(redirect_to)

    form = MembershipRejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid rejection reason.")
        return redirect(redirect_to)

    reason = str(form.cleaned_data.get("reason") or "").strip()

    _, email_error = reject_membership_request(
        membership_request=req,
        actor_username=request.user.get_username(),
        rejection_reason=reason,
        send_rejected_email=not custom_email,
    )

    target_label = _membership_request_target_label(req)
    messages.success(request, f"Rejected request for {target_label}.")

    if email_error is not None:
        messages.error(request, "Request was rejected, but the email could not be sent.")

    if req.requested_username == "":
        org = req.requested_organization

        if custom_email:
            return _custom_email_redirect(
                request=request,
                membership_request=req,
                template_name=settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
                extra_context={
                    "organization_name": org.name if org is not None else (req.requested_organization_name or ""),
                    **(organization_sponsor_email_context(organization=org) if org is not None else {}),
                    "membership_type": membership_type.name,
                    "membership_type_code": membership_type.code,
                    **freeform_message_email_context(key="rejection_reason", value=reason),
                },
                redirect_to=redirect_to,
                action_status="rejected",
            )
        return redirect(redirect_to)

    if custom_email:
        return _custom_email_redirect(
            request=request,
            membership_request=req,
            template_name=settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
            extra_context={
                "membership_type": membership_type.name,
                "membership_type_code": membership_type.code,
                **freeform_message_email_context(key="rejection_reason", value=reason),
            },
            redirect_to=redirect_to,
            action_status="rejected",
        )
    return redirect(redirect_to)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_rfi(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    req = get_object_or_404(MembershipRequest.objects.select_related("membership_type", "requested_organization"), pk=pk)
    membership_type = req.membership_type

    custom_email = bool(str(request.POST.get("custom_email") or "").strip())
    rfi_message = str(request.POST.get("rfi_message") or "").strip()

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        redirect_to = referer or reverse("membership-requests")
        if redirect_to and not url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            redirect_to = reverse("membership-requests")

    if req.status == MembershipRequest.Status.on_hold:
        target_label = _membership_request_target_label(req)
        messages.info(request, f"Request for {target_label} is already on hold.")
        return redirect(redirect_to)

    application_url = request.build_absolute_uri(reverse("membership-request-self", args=[req.pk]))

    _log, email_error = put_membership_request_on_hold(
        membership_request=req,
        actor_username=request.user.get_username(),
        rfi_message=rfi_message,
        send_rfi_email=not custom_email,
        application_url=application_url,
    )

    if custom_email:
        extra_context: dict[str, str] = {
            "membership_type": membership_type.name,
            "membership_type_code": membership_type.code,
            "rfi_message": rfi_message,
            "application_url": application_url,
        }
        extra_context.update(freeform_message_email_context(key="rfi_message", value=rfi_message))
        if req.requested_username == "":
            org = req.requested_organization
            extra_context["organization_name"] = org.name if org is not None else (req.requested_organization_name or "")
            extra_context.update(organization_sponsor_email_context(organization=org) if org is not None else {})

        return _custom_email_redirect(
            request=request,
            membership_request=req,
            template_name=settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
            extra_context=extra_context,
            redirect_to=redirect_to,
            action_status="on_hold",
        )

    target_label = _membership_request_target_label(req)
    messages.success(request, f"Sent Request for Information for {target_label}.")
    if email_error is not None:
        messages.error(request, "Request was put on hold, but the email could not be sent.")
    return redirect(redirect_to)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_ignore(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    req = get_object_or_404(MembershipRequest.objects.select_related("membership_type", "requested_organization"), pk=pk)

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        redirect_to = referer or reverse("membership-requests")
        if redirect_to and not url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            redirect_to = reverse("membership-requests")

    if req.status == MembershipRequest.Status.ignored:
        target_label = _membership_request_target_label(req)
        messages.info(request, f"Request for {target_label} is already ignored.")
        return redirect(redirect_to)

    ignore_membership_request(
        membership_request=req,
        actor_username=request.user.get_username(),
    )

    target_label = _membership_request_target_label(req)
    messages.success(request, f"Ignored request for {target_label}.")
    return redirect(redirect_to)


@permission_required(ASTRA_CHANGE_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_set_expiry(request: HttpRequest, username: str, membership_type_code: str) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    username = _normalize_str(username)
    membership_type_code = _normalize_str(membership_type_code)
    if not username or not membership_type_code:
        raise Http404("Not found")

    membership_type = get_object_or_404(MembershipType, pk=membership_type_code)

    target = FreeIPAUser.get(username)
    if target is None:
        messages.error(request, "Unable to load the requested user from FreeIPA.")
        return redirect("user-profile", username=username)

    valid_memberships = get_valid_memberships_for_username(username)
    current_membership = next(
        (m for m in valid_memberships if m.membership_type_id == membership_type.code),
        None,
    )
    if current_membership is None:
        messages.error(request, "That user does not currently have an active membership of that type.")
        return redirect("user-profile", username=username)

    form = MembershipUpdateExpiryForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid expiration date.")
        return redirect("user-profile", username=username)

    expires_on = form.cleaned_data["expires_on"]

    # The committee sets an expiration DATE. Interpret that as end-of-day UTC
    # (single source of truth), and rely on timezone conversion for display.
    expires_at = datetime.datetime.combine(expires_on, datetime.time(23, 59, 59), tzinfo=datetime.UTC)

    # If the committee sets expiry to now/past, ensure access is revoked immediately.
    now = timezone.now()
    if expires_at <= now:
        group_cn = str(membership_type.group_cn or "").strip()
        if group_cn and group_cn in target.groups_list:
            try:
                target.remove_from_group(group_name=group_cn)
            except Exception:
                logger.exception(
                    "membership_set_expiry: failed to remove user from group username=%s membership_type=%s group_cn=%s",
                    username,
                    membership_type.code,
                    group_cn,
                )
                messages.error(request, "Failed to remove the user from the FreeIPA group.")
                return redirect("user-profile", username=username)

    MembershipLog.create_for_expiry_change(
        actor_username=request.user.get_username(),
        target_username=username,
        membership_type=membership_type,
        expires_at=expires_at,
    )

    if expires_at <= now:
        from core.models import Membership

        Membership.objects.filter(target_username=username, membership_type=membership_type).delete()

    messages.success(request, "Membership expiration updated.")
    return redirect("user-profile", username=username)


@permission_required(ASTRA_DELETE_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_terminate(request: HttpRequest, username: str, membership_type_code: str) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    username = _normalize_str(username)
    membership_type_code = _normalize_str(membership_type_code)
    if not username or not membership_type_code:
        raise Http404("Not found")

    membership_type = get_object_or_404(MembershipType, pk=membership_type_code)

    target = FreeIPAUser.get(username)
    if target is None:
        messages.error(request, "Unable to load the requested user from FreeIPA.")
        return redirect("user-profile", username=username)

    valid_memberships = get_valid_memberships_for_username(username)
    current_membership = next(
        (m for m in valid_memberships if m.membership_type_id == membership_type.code),
        None,
    )
    if current_membership is None:
        messages.error(request, "That user does not currently have an active membership of that type.")
        return redirect("user-profile", username=username)

    # Keep FreeIPA group membership as the primary source of truth for access.
    # Termination must remove group membership immediately; deleting the Membership
    # row alone is not sufficient.
    group_cn = str(membership_type.group_cn or "").strip()
    if group_cn and group_cn in target.groups_list:
        try:
            target.remove_from_group(group_name=group_cn)
        except Exception:
            logger.exception(
                "membership_terminate: failed to remove user from group username=%s membership_type=%s group_cn=%s",
                username,
                membership_type.code,
                group_cn,
            )
            messages.error(request, "Failed to remove the user from the FreeIPA group.")
            return redirect("user-profile", username=username)

    MembershipLog.create_for_termination(
        actor_username=request.user.get_username(),
        target_username=username,
        membership_type=membership_type,
    )

    messages.success(request, "Membership terminated.")
    return redirect("user-profile", username=username)


@permission_required(ASTRA_CHANGE_MEMBERSHIP, login_url=reverse_lazy("users"))
def organization_sponsorship_set_expiry(request: HttpRequest, organization_id: int, membership_type_code: str) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    membership_type_code = _normalize_str(membership_type_code)
    if organization_id <= 0 or not membership_type_code:
        raise Http404("Not found")

    organization = get_object_or_404(Organization, pk=organization_id)
    membership_type = get_object_or_404(MembershipType, pk=membership_type_code)

    if organization.membership_level_id != membership_type.code:
        messages.error(request, "That organization does not currently have an active sponsorship of that type.")
        return redirect("organization-detail", organization_id=organization.pk)

    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = next_url
    else:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        redirect_to = referer or reverse("organization-detail", kwargs={"organization_id": organization.pk})
        if redirect_to and not url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            redirect_to = reverse("organization-detail", kwargs={"organization_id": organization.pk})

    form = MembershipUpdateExpiryForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid expiration date.")
        return redirect(redirect_to)

    expires_on = form.cleaned_data["expires_on"]
    expires_at = datetime.datetime.combine(expires_on, datetime.time(23, 59, 59), tzinfo=datetime.UTC)
    MembershipLog.create_for_org_expiry_change(
        actor_username=request.user.get_username(),
        target_organization=organization,
        membership_type=membership_type,
        expires_at=expires_at,
    )

    # If the committee sets expiry to now/past, ensure access is revoked immediately.
    now = timezone.now()
    if expires_at <= now:
        group_cn = str(membership_type.group_cn or "").strip()
        rep_username = str(organization.representative or "").strip()
        if group_cn and rep_username:
            rep = FreeIPAUser.get(rep_username)
            if rep is not None and group_cn in rep.groups_list:
                try:
                    rep.remove_from_group(group_name=group_cn)
                except Exception:
                    logger.exception(
                        "organization_sponsorship_set_expiry: failed to remove representative from group org_id=%s rep=%r group_cn=%r",
                        organization.pk,
                        rep_username,
                        group_cn,
                    )
                    messages.error(request, "Failed to remove the representative from the FreeIPA group.")
                    return redirect(redirect_to)

        if organization.membership_level_id == membership_type.code:
            organization.membership_level = None
            organization.save(update_fields=["membership_level"])

        OrganizationSponsorship.objects.filter(organization=organization).delete()

    messages.success(request, "Sponsorship expiration updated.")
    return redirect(redirect_to)


@permission_required(ASTRA_DELETE_MEMBERSHIP, login_url=reverse_lazy("users"))
def organization_sponsorship_terminate(request: HttpRequest, organization_id: int, membership_type_code: str) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    membership_type_code = _normalize_str(membership_type_code)
    if organization_id <= 0 or not membership_type_code:
        raise Http404("Not found")

    organization = get_object_or_404(Organization, pk=organization_id)
    membership_type = get_object_or_404(MembershipType, pk=membership_type_code)

    if organization.membership_level_id != membership_type.code:
        messages.error(request, "That organization does not currently have an active sponsorship of that type.")
        return redirect("organization-detail", organization_id=organization.pk)

    sponsorship = OrganizationSponsorship.objects.filter(organization=organization, membership_type=membership_type).first()
    if sponsorship is not None and sponsorship.expires_at is not None and sponsorship.expires_at <= timezone.now():
        messages.error(request, "That organization does not currently have an active sponsorship of that type.")
        return redirect("organization-detail", organization_id=organization.pk)

    group_cn = str(membership_type.group_cn or "").strip()
    rep_username = str(organization.representative or "").strip()
    if group_cn and rep_username:
        rep = FreeIPAUser.get(rep_username)
        if rep is not None and group_cn in rep.groups_list:
            try:
                rep.remove_from_group(group_name=group_cn)
            except Exception:
                logger.exception(
                    "organization_sponsorship_terminate: failed to remove representative from group org_id=%s rep=%r group_cn=%r",
                    organization.pk,
                    rep_username,
                    group_cn,
                )
                messages.error(request, "Failed to remove the representative from the FreeIPA group.")
                return redirect("organization-detail", organization_id=organization.pk)

    MembershipLog.create_for_org_termination(
        actor_username=request.user.get_username(),
        target_organization=organization,
        membership_type=membership_type,
    )
    organization.membership_level = None
    organization.save(update_fields=["membership_level"])

    OrganizationSponsorship.objects.filter(organization=organization).delete()

    messages.success(request, "Sponsorship terminated.")
    return redirect("organization-detail", organization_id=organization.pk)


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_stats(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/membership_stats.html",
        {
            "stats_data_url": reverse("membership-stats-data"),
        },
    )


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def membership_stats_data(_request: HttpRequest) -> HttpResponse:
    now = timezone.now()

    def compute_payload() -> dict[str, object]:
        # Keep the payload stable and purely aggregate (no per-user PII).
        all_freeipa_users = FreeIPAUser.all()
        users_by_username = {u.username: u for u in all_freeipa_users if u.username}
        active_freeipa_users = [u for u in all_freeipa_users if u.is_active]

        active_memberships = Membership.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

        summary: dict[str, int] = {
            "total_freeipa_users": len(all_freeipa_users),
            "active_individual_memberships": active_memberships.filter(
                membership_type__isIndividual=True
            )
            .values("target_username")
            .distinct()
            .count(),
            "pending_requests": MembershipRequest.objects.filter(
                status=MembershipRequest.Status.pending
            ).count(),
            "on_hold_requests": MembershipRequest.objects.filter(
                status=MembershipRequest.Status.on_hold
            ).count(),
            "expiring_soon_30_days": active_memberships.filter(expires_at__lte=now + datetime.timedelta(days=30))
            .exclude(expires_at__isnull=True)
            .values("target_username")
            .distinct()
            .count(),
            "expiring_soon_60_days": active_memberships.filter(expires_at__lte=now + datetime.timedelta(days=60))
            .exclude(expires_at__isnull=True)
            .values("target_username")
            .distinct()
            .count(),
            "expiring_soon_90_days": active_memberships.filter(expires_at__lte=now + datetime.timedelta(days=90))
            .exclude(expires_at__isnull=True)
            .values("target_username")
            .distinct()
            .count(),
            "active_org_sponsorships": OrganizationSponsorship.objects.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            ).count(),
        }

        # Membership type distribution (active memberships; distinct users per type).
        membership_type_rows = (
            active_memberships.values("membership_type_id", "membership_type__name")
            .annotate(count=Count("target_username", distinct=True))
            .order_by("membership_type__name")
        )
        membership_type_labels: list[str] = [r["membership_type__name"] for r in membership_type_rows]
        membership_type_counts: list[int] = [int(r["count"]) for r in membership_type_rows]

        # Requests trend (last 12 months).
        start = now - datetime.timedelta(days=365)
        request_rows = (
            MembershipRequest.objects.filter(requested_at__gte=start)
            .annotate(period=TruncMonth("requested_at"))
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        )
        requests_labels = [timezone.localtime(r["period"]).strftime("%Y-%m") for r in request_rows if r["period"]]
        requests_counts = [int(r["count"]) for r in request_rows]

        # Decisions outcomes trend (last 12 months), using decided_at on requests.
        decision_statuses = [
            MembershipRequest.Status.approved,
            MembershipRequest.Status.rejected,
            MembershipRequest.Status.ignored,
            MembershipRequest.Status.rescinded,
        ]
        decision_rows = (
            MembershipRequest.objects.filter(decided_at__isnull=False, decided_at__gte=start)
            .filter(status__in=decision_statuses)
            .annotate(period=TruncMonth("decided_at"))
            .values("period", "status")
            .annotate(count=Count("id"))
            .order_by("period", "status")
        )
        decision_periods = sorted({r["period"] for r in decision_rows if r["period"]})
        decision_labels = [timezone.localtime(p).strftime("%Y-%m") for p in decision_periods]
        decision_index = {(r["period"], r["status"]): int(r["count"]) for r in decision_rows}
        decision_datasets: list[dict[str, object]] = []
        for status in decision_statuses:
            decision_datasets.append(
                {
                    "label": str(status),
                    "data": [decision_index.get((p, status), 0) for p in decision_periods],
                }
            )

        # Expirations upcoming (next 12 months).
        exp_rows = (
            Membership.objects.filter(expires_at__isnull=False, expires_at__gte=now, expires_at__lte=now + datetime.timedelta(days=365))
            .annotate(period=TruncMonth("expires_at"))
            .values("period")
            .annotate(count=Count("target_username", distinct=True))
            .order_by("period")
        )

        def _next_month(period: datetime.datetime) -> datetime.datetime:
            year = period.year
            month = period.month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
            return period.replace(year=year, month=month, day=1)

        exp_index = {r["period"]: int(r["count"]) for r in exp_rows if r["period"]}
        exp_periods = sorted(exp_index)
        exp_labels: list[str] = []
        exp_counts: list[int] = []
        if exp_periods:
            current_local = timezone.localtime(now)
            current = current_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = exp_periods[-1]
            while current <= end:
                exp_labels.append(timezone.localtime(current).strftime("%Y-%m"))
                exp_counts.append(exp_index.get(current, 0))
                current = _next_month(current)

        charts: dict[str, object] = {
            "membership_types": {
                "labels": membership_type_labels,
                "counts": membership_type_counts,
            },
            "requests_trend": {
                "labels": requests_labels,
                "counts": requests_counts,
            },
            "decisions_trend": {
                "labels": decision_labels,
                "datasets": decision_datasets,
            },
            "expirations_upcoming": {
                "labels": exp_labels,
                "counts": exp_counts,
            },
        }

        def nationality_distribution(users: list[FreeIPAUser]) -> dict[str, list[object]]:
            counts: dict[str, int] = {}
            for user in users:
                status = country_code_status_from_user_data(user._user_data)
                if not status.is_valid or not status.code:
                    code = "Unknown/Unset"
                else:
                    code = status.code
                counts[code] = counts.get(code, 0) + 1

            ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            return {
                "labels": [k for k, _v in ordered],
                "counts": [v for _k, v in ordered],
            }

        active_member_usernames = set(active_memberships.values_list("target_username", flat=True).distinct())
        active_member_users: list[FreeIPAUser] = []
        for username in sorted(active_member_usernames):
            user = users_by_username.get(username)
            if user is None:
                user = FreeIPAUser.get(username)
            if user is not None and user.is_active:
                active_member_users.append(user)

        charts["nationality_all_users"] = nationality_distribution(active_freeipa_users)
        charts["nationality_active_members"] = nationality_distribution(active_member_users)

        return {
            "generated_at": timezone.localtime(now).isoformat(),
            "summary": summary,
            "charts": charts,
        }

    payload = cache.get_or_set("membership_stats:data:v4", compute_payload, timeout=300)
    return JsonResponse(payload)
