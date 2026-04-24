import logging
import secrets
from collections.abc import Collection
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Page, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_GET

from core import signals as astra_signals
from core.account_invitation_reconcile import schedule_account_invitation_accepted_signal
from core.forms_organizations import OrganizationEditForm
from core.freeipa.user import FreeIPAUser
from core.freeipa_directory import search_freeipa_users
from core.logging_extras import current_exception_log_fields
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    FreeIPARepresentativeSyncError,
    build_pending_request_context,
    compute_membership_requestability_context,
    expiring_soon_cutoff,
    get_membership_request_eligibility,
    get_valid_memberships,
    get_valid_memberships_by_organization_ids,
    remove_organization_representative_from_group_if_present,
    resolve_request_ids_by_membership_type,
)
from core.membership_notes import add_note
from core.membership_request_workflow import ignore_open_membership_requests_for_target
from core.models import (
    AccountInvitation,
    AuditLogEntry,
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    Organization,
)
from core.organization_claim import (
    build_organization_claim_url,
    read_organization_claim_token,
)
from core.organization_representative_transition import apply_organization_representative_transition
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    can_view_user_directory,
    has_any_membership_manage_permission,
    has_any_membership_permission,
    json_permission_required_any,
)
from core.templatetags.core_dict import membership_tier_class
from core.views_utils import (
    _normalize_str,
    block_action_without_coc,
    block_action_without_country_code,
    build_page_url_prefix,
    get_username,
    paginate_and_build_context,
    post_only_404,
    send_mail_url,
)

logger = logging.getLogger(__name__)


def _render_organization_claim_page(
    request: HttpRequest,
    *,
    state: str,
    organization: Organization | None = None,
) -> HttpResponse:
    return render(
        request,
        "core/organization_claim.html",
        {
            "state": state,
            "organization": organization,
            "membership_committee_email": settings.MEMBERSHIP_COMMITTEE_EMAIL,
        },
    )


@login_required
def organization_claim(request: HttpRequest, token: str) -> HttpResponse:
    username = get_username(request)
    if not username:
        raise Http404

    try:
        payload = read_organization_claim_token(token)
    except (signing.BadSignature, signing.SignatureExpired):
        return _render_organization_claim_page(request, state="invalid")

    organization = Organization.objects.filter(pk=payload.organization_id).first()
    if organization is None:
        return _render_organization_claim_page(request, state="invalid")

    if organization.status != Organization.Status.unclaimed:
        return _render_organization_claim_page(request, state="already_claimed")

    blocked = block_action_without_coc(
        request,
        username=username,
        action_label="claim this organization",
    )
    if blocked is not None:
        return blocked

    user = FreeIPAUser.get(username)
    blocked = block_action_without_country_code(
        request,
        user_data=user._user_data if user is not None else None,
        action_label="claim this organization",
    )
    if blocked is not None:
        return blocked

    if request.method != "POST":
        return _render_organization_claim_page(request, state="ready", organization=organization)

    try:
        refreshed_payload = read_organization_claim_token(token)
    except (signing.BadSignature, signing.SignatureExpired):
        return _render_organization_claim_page(request, state="invalid")

    try:
        claim_completed_at = timezone.now()
        normalized_username = _normalize_str(username).lower()
        with transaction.atomic():
            locked_organization = Organization.objects.select_for_update().get(pk=refreshed_payload.organization_id)

            if locked_organization.status != Organization.Status.unclaimed:
                return _render_organization_claim_page(
                    request,
                    state="already_claimed",
                )

            if locked_organization.claim_secret != refreshed_payload.claim_secret:
                return _render_organization_claim_page(request, state="invalid")

            rotated_claim_secret = secrets.token_urlsafe(32)
            accepted_invitation_ids: list[int] = []

            def persist_claimed_organization(transition_organization: Organization) -> None:
                transition_organization.status = Organization.Status.active
                transition_organization.claim_secret = rotated_claim_secret
                transition_organization.save()

                pending_invitations = AccountInvitation.objects.filter(
                    organization=transition_organization,
                    dismissed_at__isnull=True,
                    accepted_at__isnull=True,
                )
                accepted_invitation_ids.clear()
                accepted_invitation_ids.extend(pending_invitations.values_list("pk", flat=True))
                pending_invitations.update(
                    accepted_at=claim_completed_at,
                    accepted_username=normalized_username,
                )

            committed_organization = apply_organization_representative_transition(
                organization_id=locked_organization.pk,
                new_representative=username,
                caller_label="organization_claim",
                persist_changes=persist_claimed_organization,
            ).organization

            claimed_organization_id = committed_organization.id

            def _send_organization_claimed_signal() -> None:
                committed_organization = Organization.objects.get(pk=claimed_organization_id)
                astra_signals.organization_claimed.send(
                    sender=Organization,
                    organization=committed_organization,
                    actor=username,
                )
                for invitation_id in accepted_invitation_ids:
                    schedule_account_invitation_accepted_signal(invitation_id=invitation_id, actor=normalized_username)

            transaction.on_commit(_send_organization_claimed_signal)
    except FreeIPARepresentativeSyncError:
        messages.error(request, "Failed to update FreeIPA group membership for the representative.")
        return _render_organization_claim_page(request, state="ready", organization=organization)
    except IntegrityError:
        messages.error(
            request,
            format_html(
                'You already represent an organization and cannot claim another. <a href="mailto:{}">Contact the Membership Committee</a> if you need to create an additional organization.',
                settings.MEMBERSHIP_COMMITTEE_EMAIL,
            ),
        )
        return _render_organization_claim_page(request, state="ready", organization=organization)

    messages.success(request, "You are now the representative for this organization.")
    return redirect("organization-detail", organization_id=organization.pk)


def _is_representative(request: HttpRequest, organization: Organization) -> bool:
    username = get_username(request)
    return bool(username and username == organization.representative)


def _can_delete_organization(request: HttpRequest, organization: Organization) -> bool:
    return request.user.has_perm(ASTRA_DELETE_MEMBERSHIP) or _is_representative(request, organization)


def _can_edit_organization(request: HttpRequest, organization: Organization) -> bool:
    if has_any_membership_manage_permission(request.user):
        return True
    return _is_representative(request, organization)


def _can_access_organization(request: HttpRequest, organization: Organization) -> bool:
    return request.user.has_perm(ASTRA_VIEW_MEMBERSHIP) or _can_edit_organization(request, organization)


def _require_organization_access(request: HttpRequest, organization: Organization) -> None:
    if not _can_access_organization(request, organization):
        raise Http404


def _require_representative(request: HttpRequest, organization: Organization) -> None:
    if not _is_representative(request, organization):
        raise Http404


def _require_organization_edit_access(request: HttpRequest, organization: Organization) -> None:
    if not _can_edit_organization(request, organization):
        raise Http404


def _render_org_form(
    request: HttpRequest,
    form: OrganizationEditForm,
    *,
    organization: Organization | None,
    is_create: bool,
) -> HttpResponse:
    # Build contact-group descriptors so the template can loop instead of
    # repeating the same markup three times.
    contact_groups = [
        {
            "key": "business",
            "label": "Business",
            "description": "We will send legal and billing notices to this email address, unless you tell us otherwise.",
            "name_field": form["business_contact_name"],
            "email_field": form["business_contact_email"],
            "phone_field": form["business_contact_phone"],
        },
        {
            "key": "marketing",
            "label": "PR and marketing",
            "description": "We will contact this person about press releases and sponsor marketing benefits.",
            "name_field": form["pr_marketing_contact_name"],
            "email_field": form["pr_marketing_contact_email"],
            "phone_field": form["pr_marketing_contact_phone"],
        },
        {
            "key": "technical",
            "label": "Technical",
            "description": "We will send technical notices to this email address, unless you tell us otherwise.",
            "name_field": form["technical_contact_name"],
            "email_field": form["technical_contact_email"],
            "phone_field": form["technical_contact_phone"],
        },
    ]

    # When re-rendering after a failed POST, jump to the first tab that has
    # errors so the user sees the invalid fields immediately.
    selected_contact: str | None = None
    if form.errors:
        if "representative" in form.fields and form["representative"].errors:
            selected_contact = "representative"
        else:
            for _group in contact_groups:
                if _group["name_field"].errors or _group["email_field"].errors or _group["phone_field"].errors:
                    selected_contact = _group["key"]
                    break

    return render(
        request,
        "core/organization_form.html",
        {
            "form": form,
            "cancel_url": reverse("organizations") if is_create else "",
            "is_create": is_create,
            "organization": organization,
            "show_representatives": "representative" in form.fields,
            "contact_groups": contact_groups,
            "selected_contact": selected_contact,
        },
    )


def _filter_organization_queryset_by_search(
    orgs: QuerySet[Organization],
    *,
    q: str,
    can_manage_memberships: bool,
    matched_representative_usernames: Collection[str] | None = None,
) -> QuerySet[Organization]:
    if not q:
        return orgs

    name_query = q
    if can_manage_memberships:
        status_tokens = {
            "is:claimed": Organization.Status.active,
            "is:unclaimed": Organization.Status.unclaimed,
        }
        q_terms = q.split()
        matched_statuses = {status_tokens[term.lower()] for term in q_terms if term.lower() in status_tokens}
        membership_type_tokens = {
            term.lower().removeprefix("is:")
            for term in q_terms
            if term.lower().startswith("is:") and term.lower() not in status_tokens
        }

        matched_membership_type_codes = set(
            MembershipType.objects.filter(
                category__is_organization=True,
                code__in=membership_type_tokens,
            ).values_list("code", flat=True)
        )

        if matched_membership_type_codes:
            now = timezone.now()
            for membership_type_code in sorted(matched_membership_type_codes):
                orgs = orgs.filter(
                    Q(
                        memberships__membership_type_id=membership_type_code,
                        memberships__expires_at__isnull=True,
                    )
                    | Q(
                        memberships__membership_type_id=membership_type_code,
                        memberships__expires_at__gt=now,
                    )
                )
            orgs = orgs.distinct()

        recognized_tokens = {
            *status_tokens,
            *(f"is:{membership_type_code}" for membership_type_code in matched_membership_type_codes),
        }

        name_query = " ".join(term for term in q_terms if term.lower() not in recognized_tokens)
        if len(matched_statuses) == 1:
            orgs = orgs.filter(status=matched_statuses.pop())
        elif len(matched_statuses) > 1:
            orgs = orgs.none()

    if name_query:
        matched_representatives = {
            str(username).strip()
            for username in (matched_representative_usernames or ())
            if str(username).strip()
        }

        name_or_representative_filter = Q(name__icontains=name_query)
        if matched_representatives:
            name_or_representative_filter |= Q(representative__in=matched_representatives)

        orgs = orgs.filter(name_or_representative_filter)
    return orgs


def _build_organization_membership_card_context(
    *,
    card_orgs: QuerySet[Organization],
    request_query: object,
    query_param_value: str,
    page_param_value: str | None,
    page_param_name: str,
    can_manage_memberships: bool,
    link_to_detail: bool,
    matched_representative_usernames: Collection[str] | None = None,
) -> dict[str, object]:
    filtered_orgs = _filter_organization_queryset_by_search(
        card_orgs,
        q=query_param_value,
        can_manage_memberships=can_manage_memberships,
        matched_representative_usernames=matched_representative_usernames,
    )
    _, page_url_prefix = build_page_url_prefix(request_query, page_param=page_param_name)
    page_ctx = paginate_and_build_context(
        filtered_orgs,
        page_param_value,
        24,
        page_url_prefix=page_url_prefix,
    )

    organizations = list(page_ctx["page_obj"].object_list)
    grid_items = [
        {
            "kind": "organization",
            "organization": organization,
            "link_to_detail": link_to_detail,
        }
        for organization in organizations
    ]
    return {
        "organizations": organizations,
        "grid_items": grid_items,
        "q": query_param_value,
        "paginator": page_ctx["paginator"],
        "page_obj": page_ctx["page_obj"],
        "is_paginated": page_ctx["is_paginated"],
        "page_numbers": page_ctx["page_numbers"],
        "show_first": page_ctx["show_first"],
        "show_last": page_ctx["show_last"],
        "page_url_prefix": page_ctx["page_url_prefix"],
    }


def _build_organizations_page_context(request: HttpRequest) -> dict[str, object]:
    username = get_username(request)
    if not username and not request.user.is_authenticated:
        raise Http404

    can_manage_memberships = has_any_membership_permission(request.user)
    active_memberships = Membership.objects.filter(target_organization_id=OuterRef("pk")).active()

    orgs = Organization.objects.annotate(
        has_active_memberships=Exists(active_memberships),
        has_sponsorship_memberships=Exists(
            active_memberships.filter(membership_type__category_id="sponsorship")
        ),
    ).order_by("name", "id")

    my_organization = (
        Organization.objects.filter(representative=username)
        .order_by("name", "id")
        .first()
        if username
        else None
    )

    if can_manage_memberships:
        lower_section_link_to_detail = True
        mirror_empty_label = "No mirror sponsor members or organizations without memberships found."
    else:
        orgs = orgs.filter(
            status=Organization.Status.active,
            has_active_memberships=True,
        )
        lower_section_link_to_detail = False
        mirror_empty_label = "No mirror sponsor members found."

    q_sponsor = _normalize_str(request.GET.get("q_sponsor"))
    if not q_sponsor:
        # Keep historical deep links working while new per-card params are preferred.
        q_sponsor = _normalize_str(request.GET.get("q"))
    q_mirror = _normalize_str(request.GET.get("q_mirror"))

    page_sponsor = _normalize_str(request.GET.get("page_sponsor"))
    if not page_sponsor:
        # Keep historical deep links working while new per-card params are preferred.
        page_sponsor = _normalize_str(request.GET.get("page"))
    page_mirror = _normalize_str(request.GET.get("page_mirror")) or None

    normalized_query = request.GET.copy()
    normalized_query.pop("q", None)
    normalized_query.pop("page", None)
    if q_sponsor:
        normalized_query["q_sponsor"] = q_sponsor
    else:
        normalized_query.pop("q_sponsor", None)
    if page_sponsor:
        normalized_query["page_sponsor"] = page_sponsor
    else:
        normalized_query.pop("page_sponsor", None)
    if q_mirror:
        normalized_query["q_mirror"] = q_mirror
    else:
        normalized_query.pop("q_mirror", None)
    if page_mirror:
        normalized_query["page_mirror"] = page_mirror
    else:
        normalized_query.pop("page_mirror", None)

    representative_matches_by_query: dict[str, set[str]] = {}
    if can_view_user_directory(request.user):
        search_queries = {
            search_query
            for search_query in (q_sponsor, q_mirror)
            if search_query
        }
        for search_query in search_queries:
            representative_matches_by_query[search_query] = {
                str(user.username).strip()
                for user in search_freeipa_users(query=search_query, limit=100)
                if str(user.username).strip()
            }

    sponsor_card_context = _build_organization_membership_card_context(
        card_orgs=orgs.filter(has_sponsorship_memberships=True),
        request_query=normalized_query,
        query_param_value=q_sponsor,
        page_param_value=page_sponsor,
        page_param_name="page_sponsor",
        can_manage_memberships=can_manage_memberships,
        link_to_detail=lower_section_link_to_detail,
        matched_representative_usernames=representative_matches_by_query.get(q_sponsor, set()),
    )
    mirror_card_context = _build_organization_membership_card_context(
        card_orgs=orgs.filter(has_sponsorship_memberships=False),
        request_query=normalized_query,
        query_param_value=q_mirror,
        page_param_value=page_mirror,
        page_param_name="page_mirror",
        can_manage_memberships=can_manage_memberships,
        link_to_detail=lower_section_link_to_detail,
        matched_representative_usernames=representative_matches_by_query.get(q_mirror, set()),
    )

    visible_org_ids = {
        organization.pk
        for organization in (
            cast(list[Organization], sponsor_card_context["organizations"])
            + cast(list[Organization], mirror_card_context["organizations"])
            + ([my_organization] if my_organization is not None else [])
        )
    }
    organization_memberships_by_id = get_valid_memberships_by_organization_ids(
        organization_ids=visible_org_ids,
    )

    return {
        "sponsor_organizations": sponsor_card_context["organizations"],
        "mirror_organizations": mirror_card_context["organizations"],
        "sponsor_grid_items": sponsor_card_context["grid_items"],
        "mirror_grid_items": mirror_card_context["grid_items"],
        "my_organization": my_organization,
        "my_organization_create_url": (
            reverse("organization-create")
            if username and my_organization is None
            else None
        ),
        "q_sponsor": sponsor_card_context["q"],
        "q_mirror": mirror_card_context["q"],
        "sponsor_paginator": sponsor_card_context["paginator"],
        "sponsor_page_obj": sponsor_card_context["page_obj"],
        "sponsor_is_paginated": sponsor_card_context["is_paginated"],
        "sponsor_page_numbers": sponsor_card_context["page_numbers"],
        "sponsor_show_first": sponsor_card_context["show_first"],
        "sponsor_show_last": sponsor_card_context["show_last"],
        "sponsor_page_url_prefix": sponsor_card_context["page_url_prefix"],
        "mirror_paginator": mirror_card_context["paginator"],
        "mirror_page_obj": mirror_card_context["page_obj"],
        "mirror_is_paginated": mirror_card_context["is_paginated"],
        "mirror_page_numbers": mirror_card_context["page_numbers"],
        "mirror_show_first": mirror_card_context["show_first"],
        "mirror_show_last": mirror_card_context["show_last"],
        "mirror_page_url_prefix": mirror_card_context["page_url_prefix"],
        "sponsor_empty_label": "No AlmaLinux sponsor members found.",
        "mirror_empty_label": mirror_empty_label,
        "organization_memberships_by_id": organization_memberships_by_id,
    }


def organizations(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/organizations.html",
        _build_organizations_page_context(request),
    )


def _serialize_organization_membership_badge(membership: Membership) -> dict[str, str | None]:
    return {
        "label": membership.membership_type.name,
        "class_name": membership_tier_class(membership.membership_type.code),
        "request_url": None,
    }


def _serialize_organization_card_item(
    *,
    grid_item: dict[str, object],
    organization_memberships_by_id: dict[int, list[Membership]],
) -> dict[str, object]:
    organization = grid_item["organization"]
    if not isinstance(organization, Organization):
        raise TypeError("Expected organization grid item")

    memberships = organization_memberships_by_id.get(organization.pk, [])
    logo_url = organization.logo.url if organization.logo else ""
    return {
        "id": organization.pk,
        "name": organization.name,
        "status": organization.status,
        "detail_url": reverse("organization-detail", args=[organization.pk]),
        "logo_url": logo_url,
        "link_to_detail": bool(grid_item.get("link_to_detail", True)),
        "memberships": [_serialize_organization_membership_badge(membership) for membership in memberships],
    }


def _serialize_organizations_card(
    *,
    title: str,
    q: str,
    grid_items: list[dict[str, object]],
    paginator: Paginator,
    page_obj: Page,
    page_numbers: list[int],
    show_first: bool,
    show_last: bool,
    empty_label: str,
    organization_memberships_by_id: dict[int, list[Membership]],
) -> dict[str, object]:
    items_payload = [
        _serialize_organization_card_item(
            grid_item=grid_item,
            organization_memberships_by_id=organization_memberships_by_id,
        )
        for grid_item in grid_items
    ]

    paginator_count = int(getattr(paginator, "count", 0))
    page_number = int(getattr(page_obj, "number", 1))
    num_pages = int(getattr(paginator, "num_pages", 1))
    start_index = page_obj.start_index() if paginator_count else 0
    end_index = page_obj.end_index() if paginator_count else 0

    return {
        "title": title,
        "q": str(q or ""),
        "items": items_payload,
        "empty_label": empty_label,
        "pagination": {
            "count": paginator_count,
            "page": page_number,
            "num_pages": num_pages,
            "page_numbers": page_numbers,
            "show_first": show_first,
            "show_last": show_last,
            "has_previous": page_obj.has_previous(),
            "has_next": page_obj.has_next(),
            "previous_page_number": page_obj.previous_page_number() if page_obj.has_previous() else None,
            "next_page_number": page_obj.next_page_number() if page_obj.has_next() else None,
            "start_index": start_index,
            "end_index": end_index,
        },
    }


@require_GET
def organizations_api(request: HttpRequest) -> JsonResponse:
    context = _build_organizations_page_context(request)
    organization_memberships_by_id = context["organization_memberships_by_id"]
    if not isinstance(organization_memberships_by_id, dict):
        raise TypeError("Expected organization memberships mapping")

    my_organization_payload: dict[str, object] | None = None
    my_organization = context["my_organization"]
    if isinstance(my_organization, Organization):
        my_organization_payload = {
            "id": my_organization.pk,
            "name": my_organization.name,
            "status": my_organization.status,
            "detail_url": reverse("organization-detail", args=[my_organization.pk]),
            "logo_url": my_organization.logo.url if my_organization.logo else "",
            "link_to_detail": True,
            "memberships": [
                _serialize_organization_membership_badge(membership)
                for membership in organization_memberships_by_id.get(my_organization.pk, [])
            ],
        }

    payload = {
        "my_organization": my_organization_payload,
        "my_organization_create_url": context["my_organization_create_url"],
        "sponsor_card": _serialize_organizations_card(
            title="AlmaLinux Sponsor Members",
            q=cast(str, context["q_sponsor"]),
            grid_items=cast(list[dict[str, object]], context["sponsor_grid_items"]),
            paginator=cast(Paginator, context["sponsor_paginator"]),
            page_obj=cast(Page, context["sponsor_page_obj"]),
            page_numbers=cast(list[int], context["sponsor_page_numbers"]),
            show_first=cast(bool, context["sponsor_show_first"]),
            show_last=cast(bool, context["sponsor_show_last"]),
            empty_label=str(context["sponsor_empty_label"]),
            organization_memberships_by_id=organization_memberships_by_id,
        ),
        "mirror_card": _serialize_organizations_card(
            title="Mirror Sponsor Members",
            q=cast(str, context["q_mirror"]),
            grid_items=cast(list[dict[str, object]], context["mirror_grid_items"]),
            paginator=cast(Paginator, context["mirror_paginator"]),
            page_obj=cast(Page, context["mirror_page_obj"]),
            page_numbers=cast(list[int], context["mirror_page_numbers"]),
            show_first=cast(bool, context["mirror_show_first"]),
            show_last=cast(bool, context["mirror_show_last"]),
            empty_label=str(context["mirror_empty_label"]),
            organization_memberships_by_id=organization_memberships_by_id,
        ),
    }
    return JsonResponse(payload)


def _build_organization_detail_page_context(
    request: HttpRequest,
    *,
    organization: Organization,
) -> dict[str, object]:
    is_representative = _is_representative(request, organization)

    representative_username = _normalize_str(organization.representative)
    representative_full_name = ""
    if representative_username:
        representative_user = FreeIPAUser.get(representative_username)
        if representative_user is not None:
            representative_full_name = representative_user.full_name

    sponsorships = get_valid_memberships(organization=organization)
    expiring_soon_by = expiring_soon_cutoff()

    pending_requests = list(
        MembershipRequest.objects.select_related("membership_type")
        .filter(
            requested_organization=organization,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
        .order_by("-requested_at", "-pk")
    )
    pending_request_context = build_pending_request_context(
        pending_requests,
        is_organization=True,
    )
    pending_request_entries = pending_request_context.entries

    eligibility = get_membership_request_eligibility(organization=organization)
    requestability_context = compute_membership_requestability_context(
        organization=organization,
        eligibility=eligibility,
        held_category_ids={sponsorship.membership_type.category_id for sponsorship in sponsorships},
    )
    requestable_codes_by_category = requestability_context.requestable_codes_by_category

    sponsorship_request_id_by_type = resolve_request_ids_by_membership_type(
        organization=organization,
        membership_type_ids={s.membership_type_id for s in sponsorships},
    )

    sponsorship_entries: list[dict[str, object]] = []
    for sponsorship in sponsorships:
        sponsorship_category_id = sponsorship.membership_type.category_id
        has_pending_request_in_category = sponsorship_category_id in pending_request_context.category_ids
        suggested_tier_code = _suggest_tier_change_membership_type_code(
            current_membership_type=sponsorship.membership_type,
            requestable_codes=requestable_codes_by_category.get(sponsorship_category_id, set()),
        )
        sponsorship_entries.append({
            "sponsorship": sponsorship,
            "badge_text": str(sponsorship.membership_type_id).replace("_", " ").title(),
            "is_expiring_soon": bool(sponsorship.expires_at and sponsorship.expires_at <= expiring_soon_by),
            "pending_request": pending_request_context.by_category.get(sponsorship_category_id),
            "can_request_tier_change": (
                any(
                    code != sponsorship.membership_type.code
                    for code in requestable_codes_by_category.get(sponsorship_category_id, set())
                )
                and not has_pending_request_in_category
            ),
            "tier_change_url": (
                reverse("organization-membership-request", kwargs={"organization_id": organization.pk})
                + "?"
                + urlencode({"membership_type": suggested_tier_code})
            ),
            "request_id": sponsorship_request_id_by_type.get(sponsorship.membership_type_id),
        })

    can_edit_organization = _can_edit_organization(request, organization)
    can_delete_organization = _can_delete_organization(request, organization)
    can_request_membership = is_representative or request.user.has_perm(ASTRA_ADD_MEMBERSHIP)
    membership_can_request_any = False
    if can_request_membership:
        membership_can_request_any = requestability_context.membership_can_request_any

    contact_display_groups = [
        {
            "key": "business",
            "label": "Business",
            "name": organization.business_contact_name,
            "email": organization.business_contact_email,
            "phone": organization.business_contact_phone,
        },
        {
            "key": "marketing",
            "label": "PR and marketing",
            "name": organization.pr_marketing_contact_name,
            "email": organization.pr_marketing_contact_email,
            "phone": organization.pr_marketing_contact_phone,
        },
        {
            "key": "technical",
            "label": "Technical",
            "name": organization.technical_contact_name,
            "email": organization.technical_contact_email,
            "phone": organization.technical_contact_phone,
        },
    ]

    claim_url = ""
    can_send_claim_invitation = False
    send_claim_invitation_url = ""
    if organization.status == Organization.Status.unclaimed:
        claim_url = build_organization_claim_url(organization=organization, request=request)

        recipient_email = str(organization.primary_contact_email() or "").strip()
        if request.user.has_perm(ASTRA_ADD_SEND_MAIL) and recipient_email:
            can_send_claim_invitation = True
            send_claim_invitation_url = send_mail_url(
                to_type="manual",
                to=recipient_email,
                template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
                extra_context={
                    "invitation_action": "org_claim",
                    "invitation_org_id": str(organization.pk),
                    "organization_name": organization.name,
                    "claim_url": claim_url,
                },
                reply_to=settings.MEMBERSHIP_COMMITTEE_EMAIL,
            )

    return {
        "organization": organization,
        "representative_username": representative_username,
        "representative_full_name": representative_full_name,
        "pending_requests": pending_request_entries,
        "sponsorship_entries": sponsorship_entries,
        "sponsorships": sponsorships,
        "is_representative": is_representative,
        "can_request_membership": can_request_membership,
        "membership_can_request_any": membership_can_request_any,
        "can_edit_organization": can_edit_organization,
        "can_delete_organization": can_delete_organization,
        "contact_display_groups": contact_display_groups,
        "claim_url": claim_url,
        "can_send_claim_invitation": can_send_claim_invitation,
        "send_claim_invitation_url": send_claim_invitation_url,
    }


def _serialize_organization_detail_payload(context: dict[str, object]) -> dict[str, object]:
    organization = context["organization"]
    if not isinstance(organization, Organization):
        raise TypeError("Expected organization in detail context")

    sponsorships = cast(list[Membership], context["sponsorships"])
    representative_username = cast(str, context["representative_username"])
    representative_full_name = cast(str, context["representative_full_name"])
    contact_display_groups = cast(list[dict[str, object]], context["contact_display_groups"])

    return {
        "organization": {
            "id": organization.pk,
            "name": organization.name,
            "status": organization.status,
            "website": organization.website,
            "detail_url": reverse("organization-detail", args=[organization.pk]),
            "logo_url": organization.logo.url if organization.logo else "",
            "memberships": [
                {
                    "label": membership.membership_type.name,
                    "class_name": membership_tier_class(membership.membership_type.code),
                    "request_url": None,
                }
                for membership in sponsorships
            ],
            "representative": {
                "username": representative_username,
                "full_name": representative_full_name or representative_username,
            },
            "contact_groups": contact_display_groups,
            "address": {
                "street": organization.street,
                "city": organization.city,
                "state": organization.state,
                "postal_code": organization.postal_code,
                "country_code": organization.country_code,
            },
        },
    }


@require_GET
def organization_detail_api(request: HttpRequest, organization_id: int) -> JsonResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_organization_access(request, organization)
    context = _build_organization_detail_page_context(request, organization=organization)
    return JsonResponse(_serialize_organization_detail_payload(context))


def organization_create(request: HttpRequest) -> HttpResponse:
    username = get_username(request)
    if not username:
        raise Http404

    blocked = block_action_without_coc(
        request,
        username=username,
        action_label="create an organization",
    )
    if blocked is not None:
        return blocked

    can_select_representatives = request.user.has_perm(ASTRA_ADD_MEMBERSHIP)

    if not can_select_representatives and Organization.objects.filter(representative=username).exists():
        messages.error(
            request,
            format_html(
                'You already represent an organization and cannot create another. <a href="mailto:{}">Contact the Membership Committee</a> if you need to create an additional organization.',
                settings.MEMBERSHIP_COMMITTEE_EMAIL,
            ),
        )
        return redirect("organizations")

    if request.method == "POST":
        form = OrganizationEditForm(
            request.POST,
            request.FILES,
            can_select_representatives=can_select_representatives,
        )
        if can_select_representatives and "representative" in form.fields:
            form.fields["representative"].widget.attrs["data-ajax-url"] = reverse(
                "organization-representatives-search"
            )
        if form.is_valid():
            organization = form.save(commit=False)

            if can_select_representatives:
                selected = form.cleaned_data.get("representative") or ""
                if not selected and Organization.objects.filter(representative=username).exists():
                    form.add_error(
                        "representative",
                        "You already represent an organization. Select a different representative.",
                    )
                    return _render_org_form(request, form, organization=None, is_create=True)

                organization.representative = selected or username
            else:
                organization.representative = username

            try:
                organization.save()
            except IntegrityError:
                if can_select_representatives and "representative" in form.fields:
                    form.add_error(
                        "representative",
                        "That user is already the representative of another organization.",
                    )
                else:
                    form.add_error(
                        None,
                        format_html(
                            'You already represent an organization and cannot create another. <a href="mailto:{}">Contact the Membership Committee</a> if you need to create an additional organization.',
                            settings.MEMBERSHIP_COMMITTEE_EMAIL,
                        ),
                    )
                return _render_org_form(request, form, organization=None, is_create=True)

            created_organization_id = organization.id

            def _send_organization_created_signal() -> None:
                committed_organization = Organization.objects.get(pk=created_organization_id)
                astra_signals.organization_created.send(
                    sender=Organization,
                    organization=committed_organization,
                    actor=username,
                )

            transaction.on_commit(_send_organization_created_signal)

            messages.success(request, "Organization created.")
            if organization.representative == username:
                return redirect("organization-membership-request", organization_id=organization.pk)

            messages.info(request, "Membership requests must be submitted by the organization's representative.")
            return redirect("organization-detail", organization_id=organization.pk)
    else:
        form = OrganizationEditForm(
            can_select_representatives=can_select_representatives,
        )
        if can_select_representatives and "representative" in form.fields:
            form.fields["representative"].widget.attrs["data-ajax-url"] = reverse(
                "organization-representatives-search"
            )

    return _render_org_form(request, form, organization=None, is_create=True)


@require_GET
@json_permission_required_any({ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP})
def organization_representatives_search(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    if not q:
        return JsonResponse({"results": []})

    taken_representatives = set(
        Organization.objects.exclude(representative="").values_list("representative", flat=True)
    )

    organization_id = _normalize_str(request.GET.get("organization_id"))
    if organization_id and organization_id.isdigit():
        org = Organization.objects.filter(pk=int(organization_id)).only("representative").first()
        if org is not None:
            current_rep = str(org.representative or "").strip()
            if current_rep:
                taken_representatives.discard(current_rep)

    results: list[dict[str, str]] = []
    for user in search_freeipa_users(
        query=q,
        limit=20,
        exclude_usernames=taken_representatives,
    ):
        username = str(user.username)
        full_name = str(user.full_name)

        text = username
        if full_name and full_name != username:
            text = f"{full_name} ({username})"

        results.append({"id": username, "text": text})

    return JsonResponse({"results": results})


def organization_detail(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_organization_access(request, organization)
    return render(
        request,
        "core/organization_detail.html",
        _build_organization_detail_page_context(request, organization=organization),
    )


def _suggest_tier_change_membership_type_code(
    *,
    current_membership_type: MembershipType,
    requestable_codes: set[str],
) -> str:
    if not requestable_codes:
        return current_membership_type.code

    requestable_tiers = list(
        MembershipType.objects.filter(category=current_membership_type.category)
        .filter(code__in=requestable_codes)
        .order_by("sort_order", "code")
        .values_list("code", "sort_order")
    )
    if not requestable_tiers:
        return current_membership_type.code

    current_sort_order = current_membership_type.sort_order

    # Sponsorship tiers are ranked by ascending `sort_order` in this codebase,
    # so the next higher tier has a lower sort_order value.
    higher_ranked_tiers = [
        (code, sort_order)
        for code, sort_order in requestable_tiers
        if sort_order < current_sort_order
    ]
    if higher_ranked_tiers:
        return max(higher_ranked_tiers, key=lambda tier: (tier[1], tier[0]))[0]

    lower_ranked_tiers = [
        (code, sort_order)
        for code, sort_order in requestable_tiers
        if sort_order > current_sort_order
    ]
    if lower_ranked_tiers:
        return min(lower_ranked_tiers, key=lambda tier: (tier[1], tier[0]))[0]

    return requestable_tiers[0][0]

@post_only_404
def organization_delete(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    if not _can_delete_organization(request, organization):
        raise Http404

    # Remove representative from ALL active membership FreeIPA groups before
    # deleting. CASCADE on Membership.target_organization handles DB cleanup.
    rep_username = str(organization.representative or "").strip()
    active_memberships = list(
        Membership.objects.select_related("membership_type")
        .filter(target_organization=organization)
    )

    if rep_username and active_memberships:
        rep = FreeIPAUser.get(rep_username)
        if rep is not None:
            for membership in active_memberships:
                group_cn = str(membership.membership_type.group_cn or "").strip()
                if group_cn and group_cn in rep.groups_list:
                    outcome = remove_organization_representative_from_group_if_present(
                        representative_username=rep_username,
                        group_cn=group_cn,
                        caller_mode=FreeIPACallerMode.best_effort,
                        missing_user_policy=FreeIPAMissingUserPolicy.treat_as_noop,
                    )
                    if outcome not in {
                        FreeIPAGroupRemovalOutcome.removed,
                        FreeIPAGroupRemovalOutcome.already_not_member,
                        FreeIPAGroupRemovalOutcome.noop_blank_input,
                        FreeIPAGroupRemovalOutcome.user_not_found,
                    }:
                        logger.error(
                            "organization_delete: failed to remove representative from group org_id=%s rep=%r group_cn=%r",
                            organization.pk,
                            rep_username,
                            group_cn,
                        )
                        messages.error(request, "Failed to remove the representative from the FreeIPA group.")
                        return redirect("organization-detail", organization_id=organization.pk)

    actor_username = get_username(request)
    with transaction.atomic():
        for membership in active_memberships:
            MembershipLog.create_for_termination(
                actor_username=actor_username,
                target_organization=organization,
                membership_type=membership.membership_type,
            )

        ignore_open_membership_requests_for_target(
            organization=organization,
            actor_username=actor_username,
        )

        organization.delete()
    messages.success(request, "Organization deleted.")
    return redirect("organizations")


@post_only_404
def organization_sponsorship_extend(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_representative(request, organization)

    membership_type_code = str(request.POST.get("membership_type", "") or "").strip()
    if not membership_type_code:
        messages.error(request, "Select a membership to extend.")
        return redirect("organization-detail", organization_id=organization.pk)

    request_path = reverse("organization-membership-request", kwargs={"organization_id": organization.pk})
    return redirect(f"{request_path}?{urlencode({'membership_type': membership_type_code})}")


def organization_edit(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    old_country_code = organization.country_code
    _require_organization_edit_access(request, organization)

    can_select_representatives = request.user.has_perm(ASTRA_CHANGE_MEMBERSHIP)

    initial: dict[str, object] = {}
    if can_select_representatives and organization.representative:
        initial["representative"] = organization.representative

    form = OrganizationEditForm(
        request.POST or None,
        request.FILES or None,
        instance=organization,
        can_select_representatives=can_select_representatives,
        initial=initial,
    )
    if can_select_representatives and "representative" in form.fields:
        form.fields["representative"].widget.attrs["data-ajax-url"] = (
            reverse("organization-representatives-search") + f"?organization_id={organization.pk}"
        )

    if request.method == "POST" and form.is_valid():
        updated_org = form.save(commit=False)
        changed_fields = list(form.changed_data)

        old_representative = ""
        new_representative = ""
        persisted_organization = updated_org

        try:
            if can_select_representatives and "representative" in form.fields:
                representative = str(form.cleaned_data.get("representative") or "").strip()

                def persist_edited_organization(locked_organization: Organization) -> None:
                    for field_name in changed_fields:
                        if field_name == "representative":
                            continue
                        setattr(locked_organization, field_name, getattr(updated_org, field_name))
                    locked_organization.save()

                transition_result = apply_organization_representative_transition(
                    organization_id=organization.pk,
                    new_representative=representative,
                    caller_label="organization_edit",
                    persist_changes=persist_edited_organization,
                )
                persisted_organization = transition_result.organization
                old_representative = transition_result.old_representative
                new_representative = transition_result.new_representative
            else:
                updated_org.save()
        except IntegrityError:
            form.add_error(
                "representative",
                "That user is already the representative of another organization.",
            )
            return _render_org_form(request, form, organization=organization, is_create=False)
        except FreeIPARepresentativeSyncError:
            logger.exception(
                "organization_edit: failed to sync representative groups org_id=%s old=%r new=%r",
                organization.pk,
                organization.representative,
                form.cleaned_data.get("representative") or "",
                extra=current_exception_log_fields(),
            )
            form.add_error(None, "Failed to update FreeIPA group membership for the representative.")
            return _render_org_form(request, form, organization=organization, is_create=False)

        actor_username = get_username(request) or ""

        if changed_fields:
            try:
                AuditLogEntry.objects.create(
                    organization=organization,
                    event_type="organization_edited",
                    payload={
                        "changed_fields": changed_fields,
                        "actor_username": actor_username,
                    },
                    is_public=False,
                )
            except Exception:
                logger.exception(
                    "organization_edit: failed to create organization_edited audit entry org_id=%s changed_fields=%r actor=%r",
                    organization.pk,
                    changed_fields,
                    actor_username,
                    extra=current_exception_log_fields(),
                )

        if "country_code" in changed_fields:
            _actor = actor_username
            _old = old_country_code
            _new = persisted_organization.country_code
            transaction.on_commit(
                lambda: astra_signals.organization_country_changed.send(
                    sender=Organization,
                    organization=Organization.objects.get(pk=persisted_organization.pk),
                    old_country=_old,
                    new_country=_new,
                    actor=_actor,
                )
            )

        if old_representative and new_representative and old_representative != new_representative:
            pending_requests = list(
                MembershipRequest.objects.select_related("membership_type").filter(
                    requested_organization=organization,
                    status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                )
            )
            if pending_requests:
                MembershipLog.objects.bulk_create(
                    [
                        MembershipLog(
                            actor_username=actor_username,
                            target_username="",
                            target_organization=organization,
                            membership_type=mr.membership_type,
                            membership_request=mr,
                            action=MembershipLog.Action.representative_changed,
                        )
                        for mr in pending_requests
                    ]
                )

                if actor_username:
                    for mr in pending_requests:
                        try:
                            add_note(
                                membership_request=mr,
                                username=actor_username,
                                content=None,
                                action={
                                    "type": "representative_changed",
                                    "old": old_representative,
                                    "new": new_representative,
                                },
                            )
                        except Exception:
                            logger.exception(
                                "organization_edit: failed to create representative_changed note org_id=%s request_id=%s actor=%r old=%r new=%r",
                                organization.pk,
                                mr.pk,
                                actor_username,
                                old_representative,
                                new_representative,
                                extra=current_exception_log_fields(),
                            )
                else:
                    logger.warning(
                        "organization_edit: missing actor username for representative_changed notes org_id=%s",
                        organization.pk,
                    )

            try:
                AuditLogEntry.objects.create(
                    organization=organization,
                    event_type="organization_representative_changed",
                    payload={
                        "old": old_representative,
                        "new": new_representative,
                        "actor": actor_username,
                    },
                    is_public=False,
                )
            except Exception:
                logger.exception(
                    "organization_edit: failed to create representative_changed audit entry org_id=%s actor=%r old=%r new=%r",
                    organization.pk,
                    actor_username,
                    old_representative,
                    new_representative,
                    extra=current_exception_log_fields(),
                )

        messages.success(request, "Organization details updated.")

        return redirect("organization-detail", organization_id=organization.pk)

    return _render_org_form(request, form, organization=organization, is_create=False)
