import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from core.backends import FreeIPAUser
from core.forms_organizations import OrganizationEditForm
from core.membership import (
    build_pending_request_context,
    expiring_soon_cutoff,
    get_membership_request_eligibility,
    get_valid_memberships,
    membership_request_can_request_any,
    remove_user_from_group,
    requestable_membership_types_for_target,
    resolve_request_ids_by_membership_type,
)
from core.membership_notes import add_note
from core.models import Membership, MembershipLog, MembershipRequest, Organization
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    json_permission_required_any,
)
from core.views_utils import _normalize_str, block_action_without_coc, get_username

logger = logging.getLogger(__name__)


def _is_representative(request: HttpRequest, organization: Organization) -> bool:
    username = get_username(request)
    return bool(username and username == organization.representative)


def _can_delete_organization(request: HttpRequest, organization: Organization) -> bool:
    return request.user.has_perm(ASTRA_DELETE_MEMBERSHIP) or _is_representative(request, organization)


def _can_edit_organization(request: HttpRequest, organization: Organization) -> bool:
    if any(
        request.user.has_perm(p)
        for p in (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP)
    ):
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
        },
    )


def organizations(request: HttpRequest) -> HttpResponse:
    username = get_username(request)
    if not username:
        raise Http404

    can_manage_memberships = any(
        request.user.has_perm(p)
        for p in (
            ASTRA_ADD_MEMBERSHIP,
            ASTRA_CHANGE_MEMBERSHIP,
            ASTRA_DELETE_MEMBERSHIP,
            ASTRA_VIEW_MEMBERSHIP,
        )
    )

    if can_manage_memberships:
        orgs = Organization.objects.all().order_by("name", "id")
        empty_label = "No organizations found."
    else:
        orgs = (
            Organization.objects.filter(representative=username)
            .order_by("name", "id")
        )
        empty_label = "You don't represent any organizations yet."

    q = _normalize_str(request.GET.get("q"))

    return render(
        request,
        "core/organizations.html",
        {
            "organizations": orgs,
            "create_url": reverse("organization-create"),
            "q": q,
            "empty_label": empty_label,
        },
    )


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
            "You already represent an organization and cannot create another. Contact the membership committee if you need to create an additional organization.",
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
                        "You already represent an organization and cannot create another. Contact the membership committee if you need to create an additional organization.",
                    )
                return _render_org_form(request, form, organization=None, is_create=True)
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

    q_lower = q.lower()

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
    for u in FreeIPAUser.all():
        if not u.username:
            continue

        if u.username in taken_representatives:
            continue

        full_name = u.full_name
        if q_lower not in u.username.lower() and q_lower not in full_name.lower():
            continue

        text = u.username
        if full_name and full_name != u.username:
            text = f"{full_name} ({u.username})"

        results.append({"id": u.username, "text": text})
        if len(results) >= 20:
            break

    results.sort(key=lambda r: r["id"].lower())
    return JsonResponse({"results": results})


def organization_detail(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_organization_access(request, organization)

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
    requestable_membership_type_rows = list(
        requestable_membership_types_for_target(
            organization=organization,
            eligibility=eligibility,
        )
        .values_list("category_id", "code")
    )
    requestable_codes_by_category: dict[str, set[str]] = {}
    for category_id, code in requestable_membership_type_rows:
        category_id_value = str(category_id or "")
        code_value = str(code or "")
        if not category_id_value or not code_value:
            continue
        requestable_codes_by_category.setdefault(category_id_value, set()).add(code_value)

    sponsorship_request_id_by_type = resolve_request_ids_by_membership_type(
        organization=organization,
        membership_type_ids={s.membership_type_id for s in sponsorships},
    )

    # Build per-sponsorship display entries for the template.
    sponsorship_entries: list[dict[str, object]] = []
    for s in sponsorships:
        has_pending_request_in_category = s.category_id in pending_request_context.category_ids
        sponsorship_entries.append({
            "sponsorship": s,
            "badge_text": str(s.membership_type_id).replace("_", " ").title(),
            "is_expiring_soon": bool(s.expires_at and s.expires_at <= expiring_soon_by),
            "pending_request": pending_request_context.by_category.get(s.category_id),
            "can_request_tier_change": (
                any(
                    code != s.membership_type.code
                    for code in requestable_codes_by_category.get(s.category_id, set())
                )
                and not has_pending_request_in_category
            ),
            "tier_change_url": (
                reverse("organization-membership-request", kwargs={"organization_id": organization.pk})
                + "?"
                + urlencode({"membership_type": s.membership_type.code})
            ),
            "request_id": sponsorship_request_id_by_type.get(s.membership_type_id),
        })

    can_edit_organization = _can_edit_organization(request, organization)
    can_delete_organization = _can_delete_organization(request, organization)
    membership_can_request_any = False
    if is_representative:
        membership_can_request_any = membership_request_can_request_any(
            organization=organization,
            eligibility=eligibility,
        )
        if membership_can_request_any:
            held_category_ids = {sponsorship.category_id for sponsorship in sponsorships}
            membership_can_request_any = any(
                category_id not in held_category_ids
                for category_id, _code in requestable_membership_type_rows
            )



    # Build contact-group descriptors for looped rendering. Same pattern
    # as `contact_groups` in the edit form (see _render_org_form), but
    # with plain values instead of form fields.
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

    return render(
        request,
        "core/organization_detail.html",
        {
            "organization": organization,
            "representative_username": representative_username,
            "representative_full_name": representative_full_name,
            "pending_requests": pending_request_entries,
            "sponsorship_entries": sponsorship_entries,
            "sponsorships": sponsorships,
            "is_representative": is_representative,
            "membership_can_request_any": membership_can_request_any,
            "can_edit_organization": can_edit_organization,
            "can_delete_organization": can_delete_organization,
            "contact_display_groups": contact_display_groups,
        },
    )


def organization_delete(request: HttpRequest, organization_id: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

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
                    if not remove_user_from_group(username=rep_username, group_cn=group_cn):
                        logger.error(
                            "organization_delete: failed to remove representative from group org_id=%s rep=%r group_cn=%r",
                            organization.pk,
                            rep_username,
                            group_cn,
                        )
                        messages.error(request, "Failed to remove the representative from the FreeIPA group.")
                        return redirect("organization-detail", organization_id=organization.pk)

    actor_username = get_username(request)
    for membership in active_memberships:
        MembershipLog.create_for_termination(
            actor_username=actor_username,
            target_organization=organization,
            membership_type=membership.membership_type,
        )

    organization.delete()
    messages.success(request, "Organization deleted.")
    return redirect("organizations")


def organization_sponsorship_extend(request: HttpRequest, organization_id: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

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

        old_representative = ""
        new_representative = ""
        synced_group_cns: list[str] = []

        if can_select_representatives and "representative" in form.fields:
            representative = form.cleaned_data.get("representative") or ""
            if not representative:
                form.add_error("representative", "A representative is required.")
                return _render_org_form(request, form, organization=organization, is_create=False)

            old_representative = organization.representative
            new_representative = representative
            updated_org.representative = representative

            # Sync FreeIPA groups for ALL active memberships when the
            # representative changes.
            if old_representative and old_representative != representative:
                active_memberships = get_valid_memberships(organization=organization)
                group_cns = [
                    str(m.membership_type.group_cn or "").strip()
                    for m in active_memberships
                    if str(m.membership_type.group_cn or "").strip()
                ]

                if group_cns:
                    old_rep = FreeIPAUser.get(old_representative)
                    new_rep = FreeIPAUser.get(representative)
                    if new_rep is None:
                        form.add_error("representative", f"Unknown user: {representative}")
                        return _render_org_form(request, form, organization=organization, is_create=False)

                    # Direct FreeIPA calls are intentional here to support the rollback flow below.
                    try:
                        for gcn in group_cns:
                            if old_rep is not None and gcn in old_rep.groups_list:
                                old_rep.remove_from_group(group_name=gcn)
                            if gcn not in new_rep.groups_list:
                                new_rep.add_to_group(group_name=gcn)
                            synced_group_cns.append(gcn)
                    except Exception:
                        logger.exception(
                            "organization_edit: failed to sync representative groups org_id=%s old=%r new=%r",
                            organization.pk,
                            old_representative,
                            representative,
                        )

                        # Best-effort rollback for groups we already synced.
                        for gcn in synced_group_cns:
                            try:
                                if old_rep is not None and gcn not in old_rep.groups_list:
                                    old_rep.add_to_group(group_name=gcn)
                            except Exception:
                                logger.exception(
                                    "organization_edit: failed to rollback representative group org_id=%s old=%r group_cn=%r",
                                    organization.pk,
                                    old_representative,
                                    gcn,
                                )

                        form.add_error(None, "Failed to update FreeIPA group membership for the representative.")
                        return _render_org_form(request, form, organization=organization, is_create=False)

        try:
            updated_org.save()
        except IntegrityError:
            # Race-condition safety: DB is the source of truth.
            # Best-effort rollback if we already changed FreeIPA groups.
            if synced_group_cns and old_representative and new_representative and old_representative != new_representative:
                try:
                    old_rep = FreeIPAUser.get(old_representative)
                    new_rep = FreeIPAUser.get(new_representative)
                    for gcn in synced_group_cns:
                        if new_rep is not None and gcn in new_rep.groups_list:
                            new_rep.remove_from_group(group_name=gcn)
                        if old_rep is not None and gcn not in old_rep.groups_list:
                            old_rep.add_to_group(group_name=gcn)
                except Exception:
                    logger.exception(
                        "organization_edit: failed to rollback representative groups after IntegrityError org_id=%s old=%r new=%r",
                        organization.pk,
                        old_representative,
                        new_representative,
                    )

            form.add_error(
                "representative",
                "That user is already the representative of another organization.",
            )
            return _render_org_form(request, form, organization=organization, is_create=False)

        if old_representative and new_representative and old_representative != new_representative:
            pending_requests = list(
                MembershipRequest.objects.select_related("membership_type").filter(
                    requested_organization=organization,
                    status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                )
            )
            if pending_requests:
                actor_username = get_username(request)
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
                            )
                else:
                    logger.warning(
                        "organization_edit: missing actor username for representative_changed notes org_id=%s",
                        organization.pk,
                    )

        messages.success(request, "Organization details updated.")

        return redirect("organization-detail", organization_id=organization.pk)

    return _render_org_form(request, form, organization=organization, is_create=False)
