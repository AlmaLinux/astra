import json
import logging
from dataclasses import dataclass
from typing import Literal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html

from core.forms_membership import (
    MembershipRequestForm,
    MembershipRequestUpdateResponsesForm,
)
from core.freeipa.user import FreeIPAUser
from core.membership_constants import MembershipCategoryCode
from core.membership_request_workflow import (
    record_membership_request_created,
    rescind_membership_request,
    resubmit_membership_request,
)
from core.membership_response_normalization import normalize_membership_request_responses
from core.models import Membership, MembershipRequest, MembershipType, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.templatetags.core_membership_responses import serialize_membership_response
from core.views_utils import (
    _normalize_str,
    block_action_without_coc,
    block_action_without_country_code,
    get_username,
    post_only_404,
)

logger = logging.getLogger(__name__)


type MembershipRequestViewerMode = Literal["committee", "self_service"]


@dataclass(slots=True)
class MembershipRequestDetailState:
    username: str
    membership_request: MembershipRequest
    viewer_mode: MembershipRequestViewerMode
    payload: dict[str, object]
    bootstrap: dict[str, object]


@dataclass(slots=True)
class MembershipRequestCreateAccessContext:
    username: str
    organization: Organization | None
    is_org_request: bool
    target_username: str | None


def _membership_request_form_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def _membership_request_json_response(payload: dict[str, object], *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "private, no-cache"
    return response


def _membership_request_cancel_url(*, username: str, organization: Organization | None) -> str:
    if organization is not None:
        return reverse("organization-detail", kwargs={"organization_id": organization.pk})
    return reverse("user-profile", kwargs={"username": username})


def _serialize_membership_request_form_option_groups(
    *,
    bound_field: forms.BoundField,
) -> list[dict[str, object]]:
    category_map_raw = str(bound_field.field.widget.attrs.get("data-category-map") or "").strip()
    category_map: dict[str, str] = {}
    if category_map_raw:
        category_map = json.loads(category_map_raw)

    groups: list[dict[str, object]] = []
    current_value = "" if bound_field.value() is None else str(bound_field.value())
    for choice in bound_field.field.choices:
        group_label = choice[0]
        group_choices = choice[1]
        if isinstance(group_choices, (list, tuple)) and group_choices and isinstance(group_choices[0], (list, tuple)):
            options = [
                {
                    "value": str(option_value),
                    "label": str(option_label),
                    "selected": str(option_value) == current_value,
                    "disabled": False,
                    "category": str(category_map.get(str(option_value)) or ""),
                }
                for option_value, option_label in group_choices
            ]
            groups.append({"label": str(group_label) or None, "options": options})
            continue

        option_value = str(group_label)
        option_label = str(group_choices)
        groups.append(
            {
                "label": None,
                "options": [
                    {
                        "value": option_value,
                        "label": option_label,
                        "selected": option_value == current_value,
                        "disabled": False,
                        "category": str(category_map.get(option_value) or ""),
                    }
                ],
            }
        )

    return groups


def _serialize_membership_request_form_field(*, bound_field: forms.BoundField) -> dict[str, object]:
    widget = bound_field.field.widget
    if isinstance(widget, forms.Textarea):
        widget_type = "textarea"
    elif isinstance(widget, forms.Select):
        widget_type = "select"
    else:
        widget_type = "text"

    value = bound_field.value()
    attrs = {key: str(attr_value) for key, attr_value in widget.attrs.items() if attr_value is not None}
    payload: dict[str, object] = {
        "name": bound_field.name,
        "id": bound_field.id_for_label,
        "label": str(bound_field.label or ""),
        "widget": widget_type,
        "value": "" if value is None else str(value),
        "required": bool(bound_field.field.required),
        "disabled": bool(bound_field.field.disabled),
        "help_text": str(bound_field.help_text or ""),
        "errors": [str(error) for error in bound_field.errors],
        "attrs": attrs,
    }
    if widget_type == "select":
        payload["option_groups"] = _serialize_membership_request_form_option_groups(bound_field=bound_field)
    return payload


def _serialize_membership_request_form(*, form: MembershipRequestForm) -> dict[str, object]:
    return {
        "is_bound": form.is_bound,
        "non_field_errors": [str(error) for error in form.non_field_errors()],
        "fields": [_serialize_membership_request_form_field(bound_field=field) for field in form],
    }


def _build_membership_request_form_page_payload(
    *,
    form: MembershipRequestForm,
    organization: Organization | None,
    no_types_available: bool,
    prefill_type_unavailable_name: str | None,
) -> dict[str, object]:
    return {
        "organization": None if organization is None else {"id": organization.pk, "name": organization.name},
        "no_types_available": no_types_available,
        "prefill_type_unavailable_name": prefill_type_unavailable_name,
        "form": _serialize_membership_request_form(form=form),
    }


def _render_membership_request_form_page(
    request: HttpRequest,
    *,
    form: MembershipRequestForm,
    access_context: MembershipRequestCreateAccessContext,
    initial_payload: dict[str, object] | None = None,
) -> HttpResponse:
    organization = access_context.organization
    if organization is not None:
        api_url = reverse("api-organization-membership-request-form-detail", args=[organization.pk])
        submit_url = reverse("organization-membership-request", args=[organization.pk])
    else:
        api_url = reverse("api-membership-request-form-detail")
        submit_url = reverse("membership-request")

    return render(
        request,
        "core/membership_request.html",
        {
            "membership_request_form_title": "Request Membership",
            "membership_request_form_api_url": api_url,
            "membership_request_form_cancel_url": _membership_request_cancel_url(
                username=access_context.username,
                organization=organization,
            ),
            "membership_request_form_submit_url": submit_url,
            "membership_request_form_privacy_policy_url": reverse("privacy-policy"),
            "membership_request_form_csrf_token": get_token(request),
            "membership_request_form_initial_payload_json": None
            if initial_payload is None
            else _membership_request_form_json(initial_payload),
            "form": form,
        },
    )


def _load_membership_request_create_access_context(
    request: HttpRequest,
    *,
    organization_id: int | None = None,
    api_mode: bool = False,
) -> MembershipRequestCreateAccessContext | HttpResponse:
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

    fu = FreeIPAUser.get(username)
    if fu is None:
        messages.error(request, "Unable to load your FreeIPA profile.")
        if api_mode:
            raise Http404("User not found")
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
        if api_mode:
            raise PermissionDenied
        return blocked

    representative_user_data = representative_user._user_data if representative_user is not None else None
    requester_user_data = fu._user_data
    user_data_for_country_check = requester_user_data
    if is_org_request:
        is_requester_representative = organization is not None and username == organization.representative
        user_data_for_country_check = representative_user_data if is_requester_representative else requester_user_data

    blocked = block_action_without_country_code(
        request,
        user_data=user_data_for_country_check,
        action_label=action_label,
    )
    if blocked is not None:
        if api_mode:
            raise PermissionDenied
        return blocked

    return MembershipRequestCreateAccessContext(
        username=username,
        organization=organization,
        is_org_request=is_org_request,
        target_username=None if is_org_request else username,
    )


def _build_membership_request_get_form_payload(
    request: HttpRequest,
    *,
    access_context: MembershipRequestCreateAccessContext,
) -> tuple[MembershipRequestForm, dict[str, object]]:
    prefill_membership_type = str(request.GET.get("membership_type") or "").strip()
    organization = access_context.organization
    if access_context.is_org_request and not prefill_membership_type:
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
            username=access_context.target_username,
            organization=organization,
        )
    )

    form = MembershipRequestForm(
        username=access_context.target_username,
        organization=organization,
        initial=initial,
    )

    no_types_available = (
        not access_context.is_org_request
        and not form.fields["membership_type"].queryset.exists()
    )

    prefill_type_unavailable_name: str | None = None
    if prefill_membership_type and not access_context.is_org_request and not no_types_available:
        if not form.fields["membership_type"].queryset.filter(pk=prefill_membership_type).exists():
            blocked_type = (
                MembershipType.objects.filter(
                    pk=prefill_membership_type,
                    enabled=True,
                    category__is_individual=True,
                )
                .select_related("category")
                .first()
            )
            if blocked_type is not None:
                prefill_type_unavailable_name = blocked_type.name

    return form, _build_membership_request_form_page_payload(
        form=form,
        organization=organization,
        no_types_available=no_types_available,
        prefill_type_unavailable_name=prefill_type_unavailable_name,
    )


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

    latest = requests.select_related("membership_type").only("responses", "membership_type__category_id").order_by(
        "-requested_at",
        "-pk",
    ).first()
    if latest is None:
        return {}

    spec_by_name = MembershipRequestForm._question_spec_by_name()
    initial: dict[str, str] = {}
    normalized_responses = normalize_membership_request_responses(
        responses=latest.responses,
        is_mirror_membership=latest.membership_type.category_id == MembershipCategoryCode.mirror,
    )
    for question_name, answer in normalized_responses.entries:
        spec = spec_by_name.get(question_name)
        if spec is None:
            continue
        initial[spec.field_name] = answer

    return initial


def membership_request(request: HttpRequest, organization_id: int | None = None) -> HttpResponse:
    access_context = _load_membership_request_create_access_context(request, organization_id=organization_id)
    if isinstance(access_context, HttpResponse):
        return access_context

    username = access_context.username
    organization = access_context.organization
    is_org_request = access_context.is_org_request
    target_username = access_context.target_username
    initial_payload: dict[str, object] | None = None

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
                existing_request = (
                    MembershipRequest.objects.filter(
                        membership_type__category=membership_type.category,
                        requested_username="" if is_org_request else target_username,
                        requested_organization=organization if is_org_request else None,
                        status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                    )
                    .order_by("-requested_at", "-pk")
                    .first()
                )
                if existing_request is not None:
                    request_url = reverse("membership-request-detail", args=[existing_request.pk])
                    messages.info(
                        request,
                        format_html(
                            'A membership request is already pending for that category. '
                            '<a href="{}">View request #{}</a>.',
                            request_url,
                            existing_request.pk,
                        ),
                    )
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
                    and (
                        existing_request := (
                            MembershipRequest.objects.filter(
                                membership_type__category=posted_membership_type.category,
                                requested_username="" if is_org_request else target_username,
                                requested_organization=organization if is_org_request else None,
                                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                            )
                            .order_by("-requested_at", "-pk")
                            .first()
                        )
                    )
                ):
                    request_url = reverse("membership-request-detail", args=[existing_request.pk])
                    messages.info(
                        request,
                        format_html(
                            'A membership request is already pending for that category. '
                            '<a href="{}">View request #{}</a>.',
                            request_url,
                            existing_request.pk,
                        ),
                    )
        initial_payload = _build_membership_request_form_page_payload(
            form=form,
            organization=organization,
            no_types_available=(
                not is_org_request
                and not form.fields["membership_type"].queryset.exists()
            ),
            prefill_type_unavailable_name=None,
        )
    else:
        form, _payload = _build_membership_request_get_form_payload(
            request,
            access_context=access_context,
        )

    return _render_membership_request_form_page(
        request,
        form=form,
        access_context=access_context,
        initial_payload=initial_payload,
    )


def membership_request_form_detail_api(
    request: HttpRequest,
    organization_id: int | None = None,
) -> JsonResponse:
    if request.method != "GET":
        return _membership_request_json_response({"error": "Method not allowed."}, status=405)

    try:
        access_context = _load_membership_request_create_access_context(
            request,
            organization_id=organization_id,
            api_mode=True,
        )
    except (Http404, PermissionDenied):
        return _membership_request_json_response({"error": "Not found."}, status=404)

    if isinstance(access_context, HttpResponse):
        return _membership_request_json_response({"error": "Not found."}, status=404)

    _form, payload = _build_membership_request_get_form_payload(
        request,
        access_context=access_context,
    )
    return _membership_request_json_response(payload)


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


def _load_membership_request_for_detail(*, pk: int) -> MembershipRequest:
    return get_object_or_404(
        MembershipRequest.objects.select_related("membership_type", "requested_organization"),
        pk=pk,
    )


def _serialize_form_field(*, bound_field: forms.BoundField) -> dict[str, object]:
    widget_type = "textarea" if isinstance(bound_field.field.widget, forms.Textarea) else "text"
    value = bound_field.value()
    return {
        "name": bound_field.name,
        "label": str(bound_field.label or ""),
        "widget": widget_type,
        "value": "" if value is None else str(value),
        "required": bool(bound_field.field.required),
        "disabled": bool(bound_field.field.disabled),
        "help_text": str(bound_field.help_text or ""),
        "errors": [str(error) for error in bound_field.errors],
    }


def _serialize_update_form(*, form: MembershipRequestUpdateResponsesForm) -> dict[str, object]:
    return {
        "fields": [_serialize_form_field(bound_field=field) for field in form],
        "non_field_errors": [str(error) for error in form.non_field_errors()],
    }


def _membership_request_detail_title(
    *,
    membership_request: MembershipRequest,
    viewer_mode: MembershipRequestViewerMode,
) -> str:
    return (
        f"Membership Request #{membership_request.pk}"
        if viewer_mode == "committee"
        else f"Your Membership Request #{membership_request.pk}"
    )


def _membership_request_detail_user_profile_url_template() -> str:
    return reverse("user-profile", kwargs={"username": "__username__"})


def _membership_request_detail_organization_detail_url_template() -> str:
    sentinel = 987654321
    return reverse("organization-detail", kwargs={"organization_id": sentinel}).replace(str(sentinel), "__organization_id__")


def _membership_request_detail_back_link(
    *,
    username: str,
    membership_request: MembershipRequest,
    viewer_mode: MembershipRequestViewerMode,
) -> dict[str, str]:
    if viewer_mode == "committee":
        return {
            "url": reverse("membership-requests"),
            "label": "Back to requests",
        }

    organization = membership_request.requested_organization
    if organization is not None:
        return {
            "url": reverse("organization-detail", kwargs={"organization_id": organization.pk}),
            "label": "Back to organization",
        }

    return {
        "url": reverse("user-profile", kwargs={"username": username}),
        "label": "Back to profile",
    }


def _serialize_membership_request_target(
    *,
    membership_request: MembershipRequest,
    requested_by_username: str,
) -> dict[str, object]:
    show = bool(
        membership_request.is_organization_target
        or not requested_by_username
        or membership_request.target_identifier != requested_by_username
    )
    if not show:
        return {
            "show": False,
            "kind": "user",
            "label": "",
            "username": "",
            "organization_id": None,
            "deleted": False,
        }

    if membership_request.is_organization_target:
        organization = membership_request.requested_organization
        return {
            "show": True,
            "kind": "organization",
            "label": membership_request.organization_display_name,
            "username": "",
            "organization_id": organization.pk if organization is not None else None,
            "deleted": organization is None,
        }

    target_user = FreeIPAUser.get(membership_request.requested_username, respect_privacy=False)
    target_deleted = target_user is None
    target_full_name = target_user.full_name if target_user is not None else ""
    return {
        "show": True,
        "kind": "user",
        "label": target_full_name or membership_request.requested_username,
        "username": membership_request.requested_username,
        "organization_id": None,
        "deleted": target_deleted,
    }


def _serialize_membership_request_responses(*, membership_request: MembershipRequest) -> list[dict[str, object]]:
    normalized_responses = normalize_membership_request_responses(
        responses=membership_request.responses,
        is_mirror_membership=membership_request.membership_type.category_id == MembershipCategoryCode.mirror,
    )
    return [
        serialize_membership_response(answer, question)
        for question, answer in normalized_responses.entries
        if str(answer or "").strip()
    ]


def _membership_request_detail_committee_context(*, request: HttpRequest, membership_request: MembershipRequest) -> dict[str, object]:
    from core.views_membership.committee import (
        build_membership_request_detail_committee_context,
    )

    return build_membership_request_detail_committee_context(
        request=request,
        membership_request=membership_request,
    )


def _committee_detail_payload(
    *,
    request: HttpRequest,
    membership_request: MembershipRequest,
    context: dict[str, object],
) -> dict[str, object]:

    embargoed_country_code = str(context.get("embargoed_country_code") or "")
    embargoed_country_label = str(context.get("embargoed_country_label") or embargoed_country_code)
    compliance_warning: dict[str, str] | None = None
    if embargoed_country_code:
        compliance_warning = {
            "country_code": embargoed_country_code,
            "country_label": embargoed_country_label,
            "message": f"This user's declared country, {embargoed_country_label}, is on the list of embargoed countries.",
        }

    requested_by_username = str(context.get("requested_by_username") or "")

    return {
        "reopen": {
            "show": membership_request.status == MembershipRequest.Status.ignored and request.user.has_perm(ASTRA_ADD_MEMBERSHIP),
        },
        "compliance_warning": compliance_warning,
        "actions": {
            "canRequestInfo": bool(context.get("membership_request_can_request_info", False)),
            "showOnHoldApprove": bool(context.get("show_on_hold_approve", False)),
        },
        "requested_by": {
            "show": bool(requested_by_username),
            "username": requested_by_username,
            "full_name": str(context.get("requested_by_full_name") or ""),
            "deleted": bool(context.get("requested_by_deleted", False)),
        },
    }


def _committee_detail_bootstrap(
    *,
    request: HttpRequest,
    membership_request: MembershipRequest,
    context: dict[str, object],
) -> dict[str, object]:
    return {
        "contact_url": str(context.get("contact_url") or ""),
        "reopen_url": reverse("api-membership-request-reopen", args=[membership_request.pk]),
        "note_summary_url": reverse("api-membership-request-notes-summary", args=[membership_request.pk]),
        "note_detail_url": reverse("api-membership-request-notes", args=[membership_request.pk]),
        "note_add_url": reverse("api-membership-request-notes-add", args=[membership_request.pk]),
        "note_next_url": request.get_full_path(),
        "notes_can_view": bool(context.get("membership_can_view", False)),
        "notes_can_write": bool(context.get("membership_can_write", False)),
        "notes_can_vote": bool(context.get("membership_can_vote", False)),
        "approve_url": reverse("api-membership-request-approve", args=[membership_request.pk]),
        "approve_on_hold_url": reverse("api-membership-request-approve-on-hold", args=[membership_request.pk]),
        "reject_url": reverse("api-membership-request-reject", args=[membership_request.pk]),
        "rfi_url": reverse("api-membership-request-rfi", args=[membership_request.pk]),
        "ignore_url": reverse("api-membership-request-ignore", args=[membership_request.pk]),
    }


def _self_service_detail_payload(
    *,
    membership_request: MembershipRequest,
    username: str,
) -> dict[str, object]:
    freeipa_user = FreeIPAUser.get(username)
    user_email = freeipa_user.email if freeipa_user is not None else ""
    can_resubmit = membership_request.status == MembershipRequest.Status.on_hold
    form_payload: dict[str, object] | None = None
    if can_resubmit:
        form = MembershipRequestUpdateResponsesForm(membership_request=membership_request)
        form_payload = _serialize_update_form(form=form)

    return {
        "can_resubmit": can_resubmit,
        "can_rescind": membership_request.status in {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold},
        "committee_email": str(settings.MEMBERSHIP_COMMITTEE_EMAIL or "").strip(),
        "user_email": user_email,
        "form": form_payload,
    }


def _self_service_requested_by_payload() -> dict[str, object]:
    return {
        "show": False,
        "username": "",
        "full_name": "",
        "deleted": False,
    }


def _self_service_requested_for_payload(*, membership_request: MembershipRequest) -> dict[str, object]:
    organization = membership_request.requested_organization
    if organization is None:
        return {
            "show": False,
            "kind": "user",
            "label": "",
            "username": "",
            "organization_id": None,
            "deleted": False,
        }

    return {
        "show": True,
        "kind": "organization",
        "label": membership_request.organization_display_name,
        "username": "",
        "organization_id": organization.pk,
        "deleted": False,
    }


def _self_service_detail_bootstrap(*, membership_request: MembershipRequest) -> dict[str, object]:
    return {
        "rescind_url": reverse("membership-request-rescind", args=[membership_request.pk]),
        "form_action_url": reverse("membership-request-detail", args=[membership_request.pk]),
    }


def _build_membership_request_detail_state(request: HttpRequest, *, pk: int) -> MembershipRequestDetailState:
    username = get_username(request)
    if not username:
        raise Http404("User not found")

    membership_request = _load_membership_request_for_detail(pk=pk)
    can_view_as_committee, can_view_as_self = _membership_request_detail_access_flags(
        request,
        username=username,
        membership_request=membership_request,
    )
    if not can_view_as_committee and not can_view_as_self:
        raise Http404("Not found")

    viewer_mode: MembershipRequestViewerMode = "committee" if can_view_as_committee else "self_service"
    title = _membership_request_detail_title(
        membership_request=membership_request,
        viewer_mode=viewer_mode,
    )
    back_link = _membership_request_detail_back_link(
        username=username,
        membership_request=membership_request,
        viewer_mode=viewer_mode,
    )
    request_payload: dict[str, object] = {
        "id": membership_request.pk,
        "status": membership_request.status,
        "requested_at": membership_request.requested_at.isoformat() if membership_request.requested_at else None,
        "on_hold_at": membership_request.on_hold_at.isoformat() if membership_request.on_hold_at else None,
        "membership_type": {
            "code": membership_request.membership_type.code,
            "name": membership_request.membership_type.name,
            "category": membership_request.membership_type.category_id,
        },
        "responses": _serialize_membership_request_responses(membership_request=membership_request),
    }

    payload: dict[str, object] = {
        "viewer": {
            "mode": viewer_mode,
        },
        "request": request_payload,
    }
    bootstrap: dict[str, object] = {
        "page_title": title,
        "back_link_url": back_link["url"],
        "back_link_label": back_link["label"],
        "user_profile_url_template": _membership_request_detail_user_profile_url_template(),
        "organization_detail_url_template": _membership_request_detail_organization_detail_url_template(),
        "contact_url": "",
        "reopen_url": "",
        "note_summary_url": "",
        "note_detail_url": "",
        "note_add_url": "",
        "note_next_url": "",
        "notes_can_view": False,
        "notes_can_write": False,
        "notes_can_vote": False,
        "approve_url": "",
        "approve_on_hold_url": "",
        "reject_url": "",
        "rfi_url": "",
        "ignore_url": "",
        "rescind_url": "",
        "form_action_url": reverse("membership-request-detail", args=[membership_request.pk]),
    }

    if viewer_mode == "committee":
        committee_context = _membership_request_detail_committee_context(request=request, membership_request=membership_request)
        committee_payload = _committee_detail_payload(
            request=request,
            membership_request=membership_request,
            context=committee_context,
        )
        requested_by_payload = committee_payload.pop("requested_by")
        request_payload["decided_at"] = membership_request.decided_at.isoformat() if membership_request.decided_at else None
        request_payload["decided_by_username"] = membership_request.decided_by_username
        request_payload["requested_by"] = requested_by_payload
        request_payload["requested_for"] = _serialize_membership_request_target(
            membership_request=membership_request,
            requested_by_username=str(requested_by_payload["username"]),
        )
        payload["committee"] = committee_payload
        bootstrap.update(
            _committee_detail_bootstrap(
                request=request,
                membership_request=membership_request,
                context=committee_context,
            )
        )
    else:
        request_payload["requested_by"] = _self_service_requested_by_payload()
        request_payload["requested_for"] = _self_service_requested_for_payload(
            membership_request=membership_request,
        )
        payload["self_service"] = _self_service_detail_payload(
            membership_request=membership_request,
            username=username,
        )
        bootstrap.update(_self_service_detail_bootstrap(membership_request=membership_request))

    return MembershipRequestDetailState(
        username=username,
        membership_request=membership_request,
        viewer_mode=viewer_mode,
        payload=payload,
        bootstrap=bootstrap,
    )


def _is_json_compatibility_mode(request: HttpRequest) -> bool:
    return _normalize_str(request.headers.get("X-Astra-Compatibility-Mode")).lower() == "json"


def _render_membership_request_detail_page(
    request: HttpRequest,
    *,
    state: MembershipRequestDetailState,
    initial_payload: dict[str, object] | None = None,
) -> HttpResponse:
    return render(
        request,
        "core/membership_request_detail.html",
        {
            "membership_request_detail_api_url": reverse("api-membership-request-detail", args=[state.membership_request.pk]),
            **state.bootstrap,
            "membership_request_detail_title": str(state.bootstrap["page_title"]),
            "membership_request_detail_initial_payload_json": None
            if initial_payload is None
            else _membership_request_form_json(initial_payload),
        },
    )


def _compatibility_form_error_payload(*, form: MembershipRequestUpdateResponsesForm) -> dict[str, object]:
    return {
        "ok": False,
        "redirect_url": None,
        "reread_targets": [],
        "field_errors": {field.name: [str(error) for error in field.errors] for field in form if field.errors},
        "non_field_errors": [str(error) for error in form.non_field_errors()],
        "form": _serialize_update_form(form=form),
    }


def _handle_self_service_detail_post(
    *,
    request: HttpRequest,
    state: MembershipRequestDetailState,
) -> HttpResponse:
    membership_request = state.membership_request
    if membership_request.status != MembershipRequest.Status.on_hold:
        raise PermissionDenied

    self_service = state.payload["self_service"]

    is_json_mode = _is_json_compatibility_mode(request)
    form = MembershipRequestUpdateResponsesForm(request.POST, membership_request=membership_request)
    if not form.is_valid():
        if is_json_mode:
            payload = _compatibility_form_error_payload(form=form)
            payload["form"] = _serialize_update_form(form=form)
            return JsonResponse(payload, status=400)

        messages.error(request, "Invalid request update.")
    else:
        try:
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username=state.username,
                updated_responses=form.responses(),
            )
        except ValidationError as error:
            message = error.messages[0] if error.messages else str(error)
            form.add_error(None, message)
            if is_json_mode:
                payload = _compatibility_form_error_payload(form=form)
                payload["form"] = _serialize_update_form(form=form)
                return JsonResponse(payload, status=400)
        else:
            if is_json_mode:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": "Your request has been resubmitted for review.",
                        "redirect_url": None,
                        "reread_targets": ["detail"],
                    }
                )

            messages.success(request, "Your request has been resubmitted for review.")
            return redirect("membership-request-detail", pk=membership_request.pk)

    self_service["form"] = _serialize_update_form(form=form)
    return _render_membership_request_detail_page(request, state=state, initial_payload=state.payload)


def membership_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Canonical membership request detail view.

    Serves both committee and self-service viewers at /membership/request/<pk>/.
    Unauthorized viewers receive 404 to avoid request-existence leakage.
    """

    state = _build_membership_request_detail_state(request, pk=pk)

    if request.method == "POST":
        if state.viewer_mode != "self_service":
            raise PermissionDenied
        return _handle_self_service_detail_post(request=request, state=state)

    return _render_membership_request_detail_page(request, state=state)


def membership_request_detail_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "GET":
        response = JsonResponse({"error": "Method not allowed."}, status=405)
        response["Cache-Control"] = "private, no-cache"
        return response

    try:
        state = _build_membership_request_detail_state(request, pk=pk)
    except Http404:
        response = JsonResponse({"error": "Not found."}, status=404)
        response["Cache-Control"] = "private, no-cache"
        return response

    response = JsonResponse(state.payload)
    response["Cache-Control"] = "private, no-cache"
    return response


def membership_request_detail_legacy_redirect(request: HttpRequest, pk: int) -> HttpResponse:
    _build_membership_request_detail_state(request, pk=pk)
    return redirect("membership-request-detail", pk=pk)


def _membership_request_detail_access_flags(
    request: HttpRequest,
    *,
    username: str,
    membership_request: MembershipRequest,
) -> tuple[bool, bool]:
    can_view_as_committee = bool(request.user.has_perm(ASTRA_VIEW_MEMBERSHIP))
    can_view_as_self = _user_can_access_membership_request(username=username, membership_request=membership_request)
    return can_view_as_committee, can_view_as_self


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

    try:
        rescind_membership_request(membership_request=req, actor_username=username)
    except ValidationError as exc:
        error_message = exc.messages[0] if exc.messages else str(exc)
        messages.error(request, error_message)
    else:
        messages.success(request, "Your request has been rescinded.")

    org = req.requested_organization
    if org is not None:
        return redirect("organization-detail", organization_id=org.pk)
    return redirect("user-profile", username=username)


def membership_request_rescind_api(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    username = get_username(request)
    if not username:
        return JsonResponse({"ok": False, "error": "User not found."}, status=404)

    req = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(pk=pk)
        .first()
    )
    if req is None:
        return JsonResponse({"ok": False, "error": "Membership request not found."}, status=404)

    if not _user_can_access_membership_request(username=username, membership_request=req):
        raise Http404("Not found")

    try:
        rescind_membership_request(membership_request=req, actor_username=username)
    except ValidationError as exc:
        error_message = exc.messages[0] if exc.messages else str(exc)
        return JsonResponse({"ok": False, "error": error_message}, status=400)

    return JsonResponse({"ok": True, "message": "Your request has been rescinded."})

__all__ = [
    "membership_request",
    "membership_request_detail",
    "membership_request_detail_api",
    "membership_request_detail_legacy_redirect",
    "membership_request_rescind",
    "membership_request_rescind_api",
]
