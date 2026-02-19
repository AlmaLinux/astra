import datetime
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core.backends import FreeIPAUser
from core.country_codes import (
    country_code_status_from_user_data,
    embargoed_country_match_from_country_code,
    embargoed_country_match_from_user_data,
)
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
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    get_membership_request_eligibility,
    get_valid_memberships,
    remove_organization_representative_from_group_if_present,
    remove_user_from_group,
)
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
)
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    MEMBERSHIP_PERMISSIONS,
    has_any_membership_manage_permission,
    has_any_membership_permission,
    json_permission_required_any,
    membership_review_permissions,
)
from core.views_utils import (
    _normalize_str,
    block_action_without_coc,
    block_action_without_country_code,
    build_page_url_prefix,
    get_username,
    paginate_and_build_context,
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


def _resolve_post_redirect(
    request: HttpRequest,
    *,
    default: str,
    use_referer: bool = False,
) -> str:
    """Resolve a safe redirect URL from POST ``next``, optionally the Referer, or *default*."""
    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    if use_referer:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        candidate = referer or default
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return candidate
    return default


def _paginate_and_render_audit_log(
    request: HttpRequest,
    *,
    logs,
    q: str,
    extra_query_params: dict[str, str],
    filter_context: dict[str, str],
) -> HttpResponse:
    """Paginate audit-log results and render the shared template."""
    page_number = _normalize_str(request.GET.get("page")) or None
    query_params: dict[str, str] = {}
    if q:
        query_params["q"] = q
    query_params.update(extra_query_params)
    _, page_url_prefix = build_page_url_prefix(query_params, page_param="page")
    page_ctx = paginate_and_build_context(logs, page_number, 50, page_url_prefix=page_url_prefix)
    return render(
        request,
        "core/membership_audit_log.html",
        {
            "logs": page_ctx["page_obj"].object_list,
            "q": q,
            **filter_context,
            **page_ctx,
        },
    )


def _load_active_membership(
    *,
    membership_type: MembershipType,
    username: str | None = None,
    organization: Organization | None = None,
) -> Membership | None:
    memberships = get_valid_memberships(username=username, organization=organization)
    for membership in memberships:
        if membership.membership_type_id == membership_type.code:
            return membership
    return None


def _load_committee_membership_type(membership_type_code: str) -> MembershipType:
    normalized_code = _normalize_str(membership_type_code)
    if not normalized_code:
        raise Http404("Not found")
    return get_object_or_404(MembershipType, pk=normalized_code)


def _load_active_membership_or_redirect(
    request: HttpRequest,
    *,
    membership_type: MembershipType,
    username: str | None = None,
    organization: Organization | None = None,
    not_found_message: str,
    redirect_name: str,
    redirect_kwargs: dict[str, object],
) -> Membership | HttpResponse:
    active_membership = _load_active_membership(
        membership_type=membership_type,
        username=username,
        organization=organization,
    )
    if active_membership is None:
        messages.error(request, not_found_message)
        return redirect(redirect_name, **redirect_kwargs)
    return active_membership


def _membership_group_cn(membership_type: MembershipType) -> str:
    return str(membership_type.group_cn or "").strip()


def _remove_group_membership_if_present(
    *,
    request: HttpRequest,
    user: FreeIPAUser,
    group_cn: str,
    redirect_to: str,
    error_message: str,
    log_message: str,
    log_args: tuple[object, ...],
) -> HttpResponse | None:
    normalized_group_cn = str(group_cn or "").strip()
    if not normalized_group_cn or normalized_group_cn not in user.groups_list:
        return None
    return _remove_freeipa_group_membership(
        request=request,
        user=user,
        group_cn=normalized_group_cn,
        redirect_to=redirect_to,
        error_message=error_message,
        log_message=log_message,
        log_args=log_args,
    )


def _parse_expiry_datetime_or_redirect(
    request: HttpRequest,
    *,
    redirect_to: str,
) -> datetime.datetime | HttpResponse:
    form = MembershipUpdateExpiryForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid expiration date.")
        return redirect(redirect_to)

    expires_on = form.cleaned_data["expires_on"]
    # The committee sets an expiration DATE. Interpret that as end-of-day UTC
    # (single source of truth), and rely on timezone conversion for display.
    return datetime.datetime.combine(expires_on, datetime.time(23, 59, 59), tzinfo=datetime.UTC)


def _load_user_membership(
    request: HttpRequest,
    username: str,
    membership_type_code: str,
) -> tuple[str, MembershipType, FreeIPAUser] | HttpResponse:
    """Validate and load user + membership type for committee actions.

    Returns ``(normalized_username, membership_type, ipa_user)`` on success,
    or an ``HttpResponse`` redirect on validation failure.
    """
    username = _normalize_str(username)
    if not username:
        raise Http404("Not found")

    membership_type = _load_committee_membership_type(membership_type_code)

    target = FreeIPAUser.get(username)
    if target is None:
        messages.error(request, "Unable to load the requested user from FreeIPA.")
        return redirect("user-profile", username=username)

    active_membership = _load_active_membership_or_redirect(
        request,
        membership_type=membership_type,
        username=username,
        not_found_message="That user does not currently have an active membership of that type.",
        redirect_name="user-profile",
        redirect_kwargs={"username": username},
    )
    if isinstance(active_membership, HttpResponse):
        return active_membership

    return username, membership_type, target


def _load_organization_membership(
    request: HttpRequest,
    organization_id: int,
    membership_type_code: str,
) -> tuple[Organization, MembershipType, Membership] | HttpResponse:
    """Validate and load organization + membership type for committee actions."""
    if organization_id <= 0:
        raise Http404("Not found")

    organization = get_object_or_404(Organization, pk=organization_id)
    membership_type = _load_committee_membership_type(membership_type_code)

    active_membership = _load_active_membership_or_redirect(
        request,
        membership_type=membership_type,
        organization=organization,
        not_found_message="That organization does not currently have an active sponsorship of that type.",
        redirect_name="organization-detail",
        redirect_kwargs={"organization_id": organization.pk},
    )
    if isinstance(active_membership, HttpResponse):
        return active_membership

    return organization, membership_type, active_membership


def _remove_organization_representative_group_membership_if_present(
    *,
    request: HttpRequest,
    organization: Organization,
    membership_type: MembershipType,
    redirect_to: str,
    error_message: str,
    log_message: str,
) -> HttpResponse | None:
    group_cn = _membership_group_cn(membership_type)
    rep_username = str(organization.representative or "").strip()
    if not group_cn or not rep_username:
        return None

    outcome = remove_organization_representative_from_group_if_present(
        representative_username=rep_username,
        group_cn=group_cn,
        caller_mode=FreeIPACallerMode.best_effort,
        missing_user_policy=FreeIPAMissingUserPolicy.treat_as_noop,
    )
    if outcome in {
        FreeIPAGroupRemovalOutcome.noop_blank_input,
        FreeIPAGroupRemovalOutcome.user_not_found,
        FreeIPAGroupRemovalOutcome.already_not_member,
        FreeIPAGroupRemovalOutcome.removed,
    }:
        return None

    logger.error(log_message, organization.pk, rep_username, group_cn)
    messages.error(request, error_message)
    return redirect(redirect_to)


def _remove_freeipa_group_membership(
    *,
    request: HttpRequest,
    user: FreeIPAUser,
    group_cn: str,
    redirect_to: str,
    error_message: str,
    log_message: str,
    log_args: tuple[object, ...],
) -> HttpResponse | None:
    if not remove_user_from_group(username=user.username, group_cn=group_cn):
        logger.error(log_message, *log_args)
        messages.error(request, error_message)
        return redirect(redirect_to)
    return None


def _load_membership_request_for_action(
    request: HttpRequest,
    pk: int,
    *,
    already_status: str,
    already_label: str,
) -> tuple[MembershipRequest, str] | HttpResponse:
    """Load a membership request for a committee action view.

    Handles the POST-only guard, request loading, redirect resolution,
    and already-actioned idempotency check — repeated across approve,
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


def _pending_category_request_exists(
    *,
    membership_type: MembershipType,
    username: str | None,
    organization: Organization | None,
) -> bool:
    eligibility = get_membership_request_eligibility(
        username=username,
        organization=organization,
    )
    return membership_type.category_id in eligibility.pending_membership_category_ids


def _renewal_prefill_responses(
    *,
    membership_type_code: str,
    username: str | None,
    organization: Organization | None,
) -> dict[str, str]:
    """Return initial form values from the latest request for this renewal target/type."""
    membership_type_code = str(membership_type_code or "").strip()
    if not membership_type_code:
        return {}

    requests = MembershipRequest.objects.filter(membership_type_id=membership_type_code)
    if organization is not None:
        requests = requests.filter(requested_organization=organization)
    else:
        normalized_username = str(username or "").strip()
        if not normalized_username:
            return {}
        requests = requests.filter(requested_username=normalized_username)

    latest = requests.only("responses").order_by("-requested_at", "-pk").first()
    if latest is None:
        return {}

    spec_by_name = MembershipRequestForm._question_spec_by_name()
    initial: dict[str, str] = {}
    for item in latest.responses or []:
        if not isinstance(item, dict):
            continue
        for question, answer in item.items():
            question_name = str(question or "").strip()
            if question_name.lower() == "additional information":
                # Legacy request update forms used "Additional information";
                # canonical request forms use "Additional info".
                question_name = "Additional info"
            spec = spec_by_name.get(question_name)
            if spec is None:
                continue
            initial[spec.field_name] = str(answer or "")

    return initial


def membership_request(request: HttpRequest, organization_id: int | None = None) -> HttpResponse:
    username = get_username(request)
    if not username:
        raise Http404("User not found")

    organization = None
    if organization_id is not None:
        organization = get_object_or_404(Organization, pk=organization_id)
        if get_username(request) != organization.representative:
            raise Http404("Not found")

    is_org_request = organization is not None

    prefill_membership_type = str(request.GET.get("membership_type") or "").strip()

    fu = FreeIPAUser.get(username)
    if fu is None:
        messages.error(request, "Unable to load your FreeIPA profile.")
        return redirect("user-profile", username=username)

    representative_user: FreeIPAUser | None = fu
    if is_org_request:
        representative_username = str(organization.representative or "").strip() if organization is not None else ""
        representative_user = FreeIPAUser.get(representative_username) if representative_username else None

    action_label = "request or renew memberships"
    if is_org_request:
        action_label = "request or renew organization memberships"

    blocked = block_action_without_coc(
        request,
        username=username,
        action_label=action_label,
    )
    if blocked is not None:
        return blocked

    representative_user_data = representative_user._user_data if representative_user is not None else None
    blocked = block_action_without_country_code(
        request,
        user_data=representative_user_data,
        action_label=action_label,
    )
    if blocked is not None:
        return blocked

    target_username = None if is_org_request else username

    if request.method == "POST":
        form = MembershipRequestForm(request.POST, username=target_username, organization=organization)
        if form.is_valid():
            membership_type: MembershipType = form.cleaned_data["membership_type"]
            if not membership_type.enabled:
                form.add_error("membership_type", "That membership type is not available.")
            elif not is_org_request and not membership_type.category.is_individual:
                form.add_error("membership_type", "That membership type is not available.")
            elif is_org_request and not membership_type.category.is_organization:
                form.add_error("membership_type", "That membership type is not available.")
            elif not membership_type.group_cn:
                form.add_error("membership_type", "That membership type is not currently linked to a group.")
            else:
                if _pending_category_request_exists(
                    membership_type=membership_type,
                    username=target_username,
                    organization=organization,
                ):
                    messages.info(request, "A membership request is already pending for that category.")
                    if is_org_request:
                        return redirect("organization-detail", organization_id=organization.pk)
                    return redirect("user-profile", username=username)

                responses = form.responses()
                requested_username = "" if is_org_request else username
                mr = MembershipRequest.objects.create(
                    requested_username=requested_username,
                    requested_organization=organization,
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
                    if not is_org_request:
                        embargoed_match = embargoed_country_match_from_user_data(user_data=fu._user_data)
                        if embargoed_match is not None:
                            add_note(
                                membership_request=mr,
                                username=CUSTOS,
                                content=(
                                    "This user's declared country, "
                                    f"{embargoed_match.label}, is on the list of embargoed countries."
                                ),
                            )
                    else:
                        org_country_match = embargoed_country_match_from_country_code(
                            organization.country_code if organization is not None else ""
                        )
                        if org_country_match is not None:
                            add_note(
                                membership_request=mr,
                                username=CUSTOS,
                                content=(
                                    "This organization's declared country, "
                                    f"{org_country_match.label}, is on the list of embargoed countries."
                                ),
                            )

                        if representative_user is not None:
                            embargoed_match = embargoed_country_match_from_user_data(
                                user_data=representative_user._user_data,
                            )
                            if embargoed_match is not None:
                                add_note(
                                    membership_request=mr,
                                    username=CUSTOS,
                                    content=(
                                        "This organization's representative's declared country, "
                                        f"{embargoed_match.label}, is on the list of embargoed countries."
                                    ),
                                )
                except Exception:
                    logger.exception(
                        "Failed to record embargoed-country system note request_id=%s org_id=%s username=%s",
                        mr.pk,
                        organization.pk if organization is not None else None,
                        username,
                    )

                if is_org_request:
                    redirect_target = "organization-detail"
                    redirect_kwargs = {"organization_id": organization.pk}
                else:
                    redirect_target = "user-profile"
                    redirect_kwargs = {"username": username}

                messages.success(request, "Membership request submitted for review.")
                return redirect(redirect_target, **redirect_kwargs)
        else:
            posted_membership_type_code = _normalize_str(request.POST.get("membership_type"))
            if posted_membership_type_code:
                posted_membership_type = (
                    MembershipType.objects.select_related("category")
                    .filter(pk=posted_membership_type_code)
                    .first()
                )
                if (
                    posted_membership_type is not None
                    and _pending_category_request_exists(
                        membership_type=posted_membership_type,
                        username=target_username,
                        organization=organization,
                    )
                ):
                    messages.info(request, "A membership request is already pending for that category.")
    else:
        if is_org_request and not prefill_membership_type:
            prefill_membership_type = (
                Membership.objects.filter(target_organization=organization)
                .select_related("membership_type", "membership_type__category")
                .order_by(
                    "membership_type__category__sort_order",
                    "membership_type__sort_order",
                    "membership_type__code",
                )
                .values_list("membership_type_id", flat=True)
                .first()
                or ""
            )

        initial = {"membership_type": prefill_membership_type}
        initial.update(
            _renewal_prefill_responses(
                membership_type_code=prefill_membership_type,
                username=target_username,
                organization=organization,
            )
        )

        form = MembershipRequestForm(
            username=target_username,
            organization=organization,
            initial=initial,
        )

    cancel_url = reverse("user-profile", kwargs={"username": username})
    if is_org_request:
        cancel_url = reverse("organization-detail", kwargs={"organization_id": organization.pk})

    return render(
        request,
        "core/membership_request.html",
        {
            "form": form,
            "organization": organization,
            "cancel_url": cancel_url,
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
    username = get_username(request)
    if not username:
        raise Http404("User not found")

    req = get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )
    if not _user_can_access_membership_request(username=username, membership_request=req):
        # Avoid leaking that the request exists.
        raise Http404("Not found")

    organization = req.requested_organization

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
                "core/membership_request.html",
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
                "core/membership_request.html",
                {
                    "req": req,
                    "form": form,
                    "user_email": user_email,
                },
            )

        messages.success(request, "Your request has been resubmitted for review.")
        return redirect("membership-request-self", pk=req.pk)

    form = MembershipRequestUpdateResponsesForm(membership_request=req)
    if req.status != MembershipRequest.Status.on_hold:
        # For non-editable requests (pending/accepted/etc), render a read-only version of the
        # responses using the same form visualization as the on-hold update screen.
        has_additional_info = any(
            isinstance(item, dict)
            and any(str(key).strip().lower() == "additional information" for key in item)
            for item in (req.responses or [])
        )
        if not has_additional_info and "q_additional_information" in form.fields:
            del form.fields["q_additional_information"]

        for field in form.fields.values():
            field.disabled = True
            field.required = False

    return render(
        request,
        "core/membership_request.html",
        {
            "req": req,
            "form": form,
            "user_email": user_email,
            "organization": organization,
            "cancel_url": reverse("organization-detail", kwargs={"organization_id": organization.pk})
            if organization is not None
            else reverse("user-profile", kwargs={"username": username}),
        },
    )


@post_only_404
def membership_request_rescind(request: HttpRequest, pk: int) -> HttpResponse:
    username = get_username(request)
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

    org = req.requested_organization
    if org is not None:
        return redirect("organization-detail", organization_id=org.pk)
    return redirect("user-profile", username=username)


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    username = _normalize_str(request.GET.get("username"))
    raw_org = _normalize_str(request.GET.get("organization"))
    organization_id = int(raw_org) if raw_org.isdigit() else None

    logs = MembershipLog.objects.select_related(
        "membership_type",
        "membership_request",
        "membership_request__membership_type",
        "target_organization",
    ).all()
    if username:
        logs = logs.filter(target_username=username)
    if organization_id is not None:
        logs = logs.for_organization_identifier(organization_id)
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
    extra_query_params: dict[str, str] = {}
    if username:
        extra_query_params["username"] = username
    if organization_id is not None:
        extra_query_params["organization"] = str(organization_id)
    organization_str = str(organization_id) if organization_id is not None else ""
    return _paginate_and_render_audit_log(
        request,
        logs=logs,
        q=q,
        extra_query_params=extra_query_params,
        filter_context={
            "filter_username": username,
            "filter_username_param": username,
            "filter_organization": organization_str,
            "filter_organization_param": organization_str,
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_organization(request: HttpRequest, organization_id: int) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))

    logs = (
        MembershipLog.objects.select_related(
            "membership_type",
            "membership_request",
            "membership_request__membership_type",
            "target_organization",
        )
        .for_organization_identifier(organization_id)
        .order_by("-created_at")
    )
    if q:
        logs = logs.filter(
            Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    return _paginate_and_render_audit_log(
        request,
        logs=logs,
        q=q,
        extra_query_params={},
        filter_context={
            "filter_username": "",
            "filter_username_param": "",
            "filter_organization": str(organization_id),
            "filter_organization_param": str(organization_id),
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_user(request: HttpRequest, username: str) -> HttpResponse:
    username = _normalize_str(username)
    q = _normalize_str(request.GET.get("q"))

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

    return _paginate_and_render_audit_log(
        request,
        logs=logs,
        q=q,
        extra_query_params={},
        filter_context={
            "filter_username": username,
            "filter_username_param": "",
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
def membership_request_reject(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="reject")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_rfi(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="rfi")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_request_ignore(request: HttpRequest, pk: int) -> HttpResponse:
    return run_membership_request_action(request, pk, action="ignore")


@permission_required(ASTRA_CHANGE_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_set_expiry(
    request: HttpRequest,
    username: str = "",
    membership_type_code: str = "",
    organization_id: int | None = None,
) -> HttpResponse:
    if organization_id is not None:
        result = _load_organization_membership(request, organization_id, membership_type_code)
        if isinstance(result, HttpResponse):
            return result
        organization, membership_type, _active_membership = result

        redirect_to = _resolve_post_redirect(
            request,
            default=reverse("organization-detail", kwargs={"organization_id": organization.pk}),
            use_referer=True,
        )

        expires_at = _parse_expiry_datetime_or_redirect(request, redirect_to=redirect_to)
        if isinstance(expires_at, HttpResponse):
            return expires_at

        should_terminate_now = expires_at.date() <= timezone.localdate()
        if should_terminate_now:
            error_redirect = _remove_organization_representative_group_membership_if_present(
                request=request,
                organization=organization,
                membership_type=membership_type,
                redirect_to=redirect_to,
                error_message="Failed to remove the representative from the FreeIPA group.",
                log_message=(
                    "organization_sponsorship_set_expiry: failed to remove representative from group "
                    "org_id=%s rep=%r group_cn=%r"
                ),
            )
            if error_redirect is not None:
                return error_redirect

        MembershipLog.create_for_expiry_change(
            actor_username=get_username(request),
            membership_type=membership_type,
            expires_at=expires_at,
            target_organization=organization,
        )

        if should_terminate_now:
            messages.success(request, "Sponsorship expiration updated.")
            return redirect(redirect_to)

        messages.success(request, "Sponsorship expiration updated.")
        return redirect(redirect_to)

    result = _load_user_membership(request, username, membership_type_code)
    if isinstance(result, HttpResponse):
        return result
    username, membership_type, _target = result
    redirect_to = reverse("user-profile", kwargs={"username": username})

    expires_at = _parse_expiry_datetime_or_redirect(request, redirect_to=redirect_to)
    if isinstance(expires_at, HttpResponse):
        return expires_at

    should_terminate_now = expires_at.date() <= timezone.localdate()
    if should_terminate_now:
        group_cn = _membership_group_cn(membership_type)
        error_redirect = _remove_group_membership_if_present(
            request=request,
            user=_target,
            group_cn=group_cn,
            redirect_to=redirect_to,
            error_message="Failed to remove the user from the FreeIPA group.",
            log_message=(
                "membership_set_expiry: failed to remove user from group username=%s "
                "membership_type=%s group_cn=%s"
            ),
            log_args=(username, membership_type.code, group_cn),
        )
        if error_redirect is not None:
            return error_redirect

    MembershipLog.create_for_expiry_change(
        actor_username=get_username(request),
        membership_type=membership_type,
        expires_at=expires_at,
        target_username=username,
    )

    if should_terminate_now:
        messages.success(request, "Membership expiration updated.")
        return redirect(redirect_to)

    messages.success(request, "Membership expiration updated.")
    return redirect(redirect_to)


@permission_required(ASTRA_DELETE_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_terminate(
    request: HttpRequest,
    username: str = "",
    membership_type_code: str = "",
    organization_id: int | None = None,
    redirect_to: str | None = None,
) -> HttpResponse:
    if organization_id is not None:
        result = _load_organization_membership(request, organization_id, membership_type_code)
        if isinstance(result, HttpResponse):
            return result
        organization, membership_type, _active_membership = result
        resolved_redirect = redirect_to or reverse("organization-detail", kwargs={"organization_id": organization.pk})

        error_redirect = _remove_organization_representative_group_membership_if_present(
            request=request,
            organization=organization,
            membership_type=membership_type,
            redirect_to=resolved_redirect,
            error_message="Failed to remove the representative from the FreeIPA group.",
            log_message=(
                "organization_sponsorship_terminate: failed to remove representative from group "
                "org_id=%s rep=%r group_cn=%r"
            ),
        )
        if error_redirect is not None:
            return error_redirect

        MembershipLog.create_for_termination(
            actor_username=get_username(request),
            membership_type=membership_type,
            target_organization=organization,
        )

        messages.success(request, "Sponsorship terminated.")
        return redirect(resolved_redirect)

    result = _load_user_membership(request, username, membership_type_code)
    if isinstance(result, HttpResponse):
        return result
    username, membership_type, target = result
    resolved_redirect = redirect_to or reverse("user-profile", kwargs={"username": username})

    group_cn = _membership_group_cn(membership_type)
    error_redirect = _remove_group_membership_if_present(
        request=request,
        user=target,
        group_cn=group_cn,
        redirect_to=resolved_redirect,
        error_message="Failed to remove the user from the FreeIPA group.",
        log_message=(
            "membership_terminate: failed to remove user from group username=%s "
            "membership_type=%s group_cn=%s"
        ),
        log_args=(username, membership_type.code, group_cn),
    )
    if error_redirect is not None:
        return error_redirect

    MembershipLog.create_for_termination(
        actor_username=get_username(request),
        membership_type=membership_type,
        target_username=username,
    )

    messages.success(request, "Membership terminated.")
    return redirect(resolved_redirect)


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_stats(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/membership_stats.html",
        {
            "stats_data_url": reverse("membership-stats-data"),
        },
    )


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_sponsors_list(request: HttpRequest) -> HttpResponse:
    active_sponsorships = list(
        Membership.objects.active()
        .filter(category_id="sponsorship")
        .select_related("target_organization", "membership_type")
        .order_by("expires_at")
    )

    representative_usernames = {
        str(membership.target_organization.representative or "").strip()
        for membership in active_sponsorships
        if membership.target_organization is not None and str(membership.target_organization.representative or "").strip()
    }

    representative_full_names: dict[str, str] = {}
    for username in sorted(representative_usernames):
        try:
            representative = FreeIPAUser.get(username)
        except Exception:
            logger.exception(
                "membership_sponsors_list: failed to fetch representative from FreeIPA username=%s",
                username,
            )
            continue

        if representative is None:
            continue

        full_name = str(representative.full_name or "").strip()
        if full_name:
            representative_full_names[username] = full_name

    now = timezone.now()
    warning_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
    memberships: list[dict[str, object]] = []
    for membership in active_sponsorships:
        organization = membership.target_organization
        if organization is None:
            continue

        rep_username = str(organization.representative or "").strip()
        rep_fullname = representative_full_names.get(rep_username, "")

        days_left: int | None = None
        is_expiring_soon = False
        if membership.expires_at is not None:
            days_left = (membership.expires_at - now).days
            is_expiring_soon = days_left <= warning_days

        memberships.append(
            {
                "organization": organization,
                "membership": membership,
                "rep_username": rep_username,
                "rep_fullname": rep_fullname,
                "days_left": days_left,
                "is_expiring_soon": is_expiring_soon,
            }
        )

    return render(
        request,
        "core/sponsorship_list.html",
        {
            "memberships": memberships,
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

        active_memberships = Membership.objects.active()

        summary: dict[str, int] = {
            "total_freeipa_users": len(all_freeipa_users),
            "active_individual_memberships": active_memberships.filter(
                membership_type__category__is_individual=True
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
            "active_org_sponsorships": Membership.objects.active()
            .filter(target_organization__isnull=False)
            .count(),
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
