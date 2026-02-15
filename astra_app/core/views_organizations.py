import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.backends import FreeIPAUser
from core.forms_organizations import OrganizationEditForm
from core.freeipa_directory import search_freeipa_users
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
    remove_organization_representative_from_group_if_present,
    resolve_request_ids_by_membership_type,
    rollback_organization_representative_groups,
    sync_organization_representative_groups,
)
from core.membership_notes import add_note
from core.models import AccountInvitation, Membership, MembershipLog, MembershipRequest, Organization
from core.organization_claim import (
    build_organization_claim_url,
    read_organization_claim_token,
)
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    has_any_membership_manage_permission,
    has_any_membership_permission,
    json_permission_required_any,
)
from core.rate_limit import allow_request
from core.views_account_invitations import send_organization_claim_invitation
from core.views_utils import (
    _normalize_str,
    block_action_without_coc,
    block_action_without_country_code,
    get_username,
    post_only_404,
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
        return _render_organization_claim_page(request, state="already_claimed", organization=organization)

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
        with transaction.atomic():
            locked_organization = Organization.objects.select_for_update().get(pk=refreshed_payload.organization_id)

            if locked_organization.status != Organization.Status.unclaimed:
                return _render_organization_claim_page(
                    request,
                    state="already_claimed",
                    organization=locked_organization,
                )

            if locked_organization.claim_secret != refreshed_payload.claim_secret:
                return _render_organization_claim_page(request, state="invalid")

            locked_organization.representative = username
            locked_organization.status = Organization.Status.active
            locked_organization.claim_secret = secrets.token_urlsafe(32)
            locked_organization.save(update_fields=["representative", "status", "claim_secret"])

            AccountInvitation.objects.filter(
                organization=locked_organization,
                dismissed_at__isnull=True,
                accepted_at__isnull=True,
            ).update(accepted_at=claim_completed_at)
    except IntegrityError:
        messages.error(
            request,
            "You already represent an organization and cannot claim another. Contact the membership committee if you need to create an additional organization.",
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

    can_manage_memberships = has_any_membership_permission(request.user)

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
        held_category_ids={sponsorship.category_id for sponsorship in sponsorships},
    )
    requestable_codes_by_category = requestability_context.requestable_codes_by_category

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
        membership_can_request_any = requestability_context.membership_can_request_any



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

    claim_url = ""
    can_send_claim_invitation = False
    send_claim_invitation_action = ""
    if organization.status == Organization.Status.unclaimed:
        claim_url = build_organization_claim_url(organization=organization, request=request)

        if request.user.has_perm(ASTRA_ADD_SEND_MAIL):
            can_send_claim_invitation = True
            send_claim_invitation_action = reverse(
                "organization-send-claim-invitation",
                args=[organization.pk],
            )

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
            "claim_url": claim_url,
            "can_send_claim_invitation": can_send_claim_invitation,
            "send_claim_invitation_action": send_claim_invitation_action,
        },
    )


@post_only_404
def organization_send_claim_invitation(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_organization_access(request, organization)

    if not request.user.has_perm(ASTRA_ADD_SEND_MAIL):
        raise Http404

    if organization.status != Organization.Status.unclaimed:
        messages.error(request, "Only unclaimed organizations can receive claim invitations.")
        return redirect("organization-detail", organization_id=organization.pk)

    actor_username = get_username(request)
    if not allow_request(
        scope="organization_claim_invitation_send",
        key_parts=[actor_username, str(organization.pk)],
        limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
        window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
    ):
        messages.error(request, "Too many send attempts. Try again shortly.")
        return redirect("organization-detail", organization_id=organization.pk)

    recipient_email = str(organization.primary_contact_email() or "").strip()
    if not recipient_email:
        messages.error(request, "This organization has no contact email to send an invitation.")
        return redirect("organization-detail", organization_id=organization.pk)

    result, existing_invitation = send_organization_claim_invitation(
        organization=organization,
        actor_username=actor_username,
        recipient_email=recipient_email,
        now=timezone.now(),
    )

    if result == "queued":
        messages.success(request, "Claim invitation queued.")
    elif result == "config_error":
        messages.error(
            request,
            "Claim invitation email configuration error: PUBLIC_BASE_URL must be configured to build invitation links.",
        )
    elif result == "invalid_email":
        messages.error(request, "The organization contact email is invalid.")
    elif result == "conflict":
        if existing_invitation is not None and existing_invitation.organization_id is not None:
            account_invitations_url = reverse("account-invitations")
            messages.error(
                request,
                "An invitation for this email is already linked to another organization. "
                "Use a different contact email or resolve the existing invitation in "
                f"Account Invitations ({account_invitations_url}).",
            )
        else:
            messages.error(request, "Unable to send claim invitation due to invitation linkage conflict.")
    else:
        messages.error(request, "Failed to queue claim invitation.")

    return redirect("organization-detail", organization_id=organization.pk)


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
    for membership in active_memberships:
        MembershipLog.create_for_termination(
            actor_username=actor_username,
            target_organization=organization,
            membership_type=membership.membership_type,
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
        representative_group_sync_journal = None

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
                    try:
                        sync_result = sync_organization_representative_groups(
                            old_representative=old_representative,
                            new_representative=representative,
                            group_cns=tuple(group_cns),
                            caller_mode=FreeIPACallerMode.raise_on_error,
                            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                        )
                        representative_group_sync_journal = sync_result.journal
                    except FreeIPARepresentativeSyncError as exc:
                        logger.exception(
                            "organization_edit: failed to sync representative groups org_id=%s old=%r new=%r",
                            organization.pk,
                            old_representative,
                            representative,
                        )
                        rollback_organization_representative_groups(
                            old_representative=old_representative,
                            new_representative=representative,
                            journal=exc.result.journal,
                        )

                        form.add_error(None, "Failed to update FreeIPA group membership for the representative.")
                        return _render_org_form(request, form, organization=organization, is_create=False)

        try:
            updated_org.save()
        except IntegrityError:
            # Race-condition safety: DB is the source of truth.
            # Best-effort rollback if we already changed FreeIPA groups.
            if (
                representative_group_sync_journal is not None
                and old_representative
                and new_representative
                and old_representative != new_representative
            ):
                rollback_organization_representative_groups(
                    old_representative=old_representative,
                    new_representative=new_representative,
                    journal=representative_group_sync_journal,
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
