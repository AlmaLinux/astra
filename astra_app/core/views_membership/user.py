import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.country_codes import (
    embargoed_country_match_from_country_code,
    embargoed_country_match_from_user_data,
)
from core.forms_membership import MembershipRequestForm, MembershipRequestUpdateResponsesForm
from core.freeipa.user import FreeIPAUser
from core.membership import get_membership_request_eligibility
from core.membership_notes import CUSTOS, add_note
from core.membership_request_workflow import (
    record_membership_request_created,
    rescind_membership_request,
    resubmit_membership_request,
)
from core.models import Membership, MembershipRequest, MembershipType, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.views_utils import (
    _normalize_str,
    block_action_without_coc,
    block_action_without_country_code,
    get_username,
    post_only_404,
)

logger = logging.getLogger(__name__)


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
    can_request_for_organization = request.user.has_perm(ASTRA_ADD_MEMBERSHIP)
    if organization_id is not None:
        organization = get_object_or_404(Organization, pk=organization_id)
        if username != organization.representative and not can_request_for_organization:
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
    requester_user_data = fu._user_data
    user_data_for_country_check = requester_user_data
    if is_org_request:
        is_requester_representative = organization is not None and username == organization.representative
        # Committee users can request on behalf of an organization; in that case,
        # validate the actor's own country code because they are initiating the action.
        user_data_for_country_check = representative_user_data if is_requester_representative else requester_user_data

    blocked = block_action_without_country_code(
        request,
        user_data=user_data_for_country_check,
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

__all__ = [
    "membership_request",
    "membership_request_rescind",
    "membership_request_self",
]
