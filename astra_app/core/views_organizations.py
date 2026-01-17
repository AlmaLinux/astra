from __future__ import annotations

import datetime
import logging
from typing import override

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.backends import FreeIPAUser
from core.membership_request_workflow import record_membership_request_created
from core.models import MembershipLog, MembershipRequest, MembershipType, Organization, OrganizationSponsorship
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    json_permission_required_any,
)
from core.user_labels import user_choice_from_freeipa
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


def _can_access_organization(request: HttpRequest, organization: Organization) -> bool:
    username = str(request.user.get_username() or "").strip()
    if not username:
        return False

    if request.user.has_perm(ASTRA_VIEW_MEMBERSHIP):
        return True

    return username == organization.representative


def _require_organization_access(request: HttpRequest, organization: Organization) -> None:
    if not _can_access_organization(request, organization):
        # Hide existence of organizations from unauthorized users.
        raise Http404


def _require_representative(request: HttpRequest, organization: Organization) -> None:
    username = str(request.user.get_username() or "").strip()
    if not username or username != organization.representative:
        raise Http404


def _can_edit_organization(request: HttpRequest, organization: Organization) -> bool:
    if any(
        request.user.has_perm(p)
        for p in (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP)
    ):
        return True

    username = str(request.user.get_username() or "").strip()
    if not username:
        return False

    return username == organization.representative


def _require_organization_edit_access(request: HttpRequest, organization: Organization) -> None:
    if not _can_edit_organization(request, organization):
        raise Http404


class OrganizationEditForm(forms.ModelForm):
    representative = forms.ChoiceField(
        required=False,
        widget=forms.Select(
            attrs={
                "class": "form-control alx-select2",
                "data-placeholder": "Search users…",
            }
        ),
        help_text="Select the FreeIPA user who will be the organization's representative.",
    )

    class Meta:
        model = Organization
        fields = (
            "business_contact_name",
            "business_contact_email",
            "business_contact_phone",
            "pr_marketing_contact_name",
            "pr_marketing_contact_email",
            "pr_marketing_contact_phone",
            "technical_contact_name",
            "technical_contact_email",
            "technical_contact_phone",
            "membership_level",
            "name",
            "website_logo",
            "website",
            "logo",
            "additional_information",
        )

        labels = {
            "business_contact_name": "Name",
            "business_contact_email": "Email",
            "business_contact_phone": "Phone",
            "pr_marketing_contact_name": "Name",
            "pr_marketing_contact_email": "Email",
            "pr_marketing_contact_phone": "Phone",
            "technical_contact_name": "Name",
            "technical_contact_email": "Email",
            "technical_contact_phone": "Phone",
            "membership_level": "Sponsorship Level",
            "name": "Legal/Official name of the sponsor to be listed",
            "website_logo": "High-quality logo that you would like used on the website",
            "website": "URL we should link to",
            "logo": "Logo upload for AlmaLinux Accounts",
            "additional_information": "Please provide any additional information the Membership Committee should take into account",
        }

        help_texts = {
            "website_logo": "Please provide a white logo, or a link to all of your logo options",
            "website": "Please provide the exact URL that you would like the logo to link to - this can be a dedicated page or just your primary URL",
        }

    @override
    def __init__(self, *args, **kwargs):
        self.can_select_representatives: bool = bool(kwargs.pop("can_select_representatives", False))
        super().__init__(*args, **kwargs)

        self.fields["membership_level"].queryset = MembershipType.objects.filter(isOrganization=True).order_by(
            "sort_order",
            "code",
        )

        self.fields["membership_level"].label_from_instance = (
            lambda membership_type: membership_type.description or membership_type.name
        )

        self.fields["business_contact_name"].required = True
        self.fields["business_contact_email"].required = True
        self.fields["pr_marketing_contact_name"].required = True
        self.fields["pr_marketing_contact_email"].required = True
        self.fields["technical_contact_name"].required = True
        self.fields["technical_contact_email"].required = True
        self.fields["membership_level"].required = False
        self.fields["name"].required = True
        self.fields["website_logo"].required = True
        self.fields["website"].required = True

        for field in self.fields.values():
            if isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.setdefault("class", "form-control-file")
            else:
                field.widget.attrs.setdefault("class", "form-control")

        self.fields["website"].widget = forms.URLInput(attrs={"class": "form-control", "placeholder": "https://…"})
        self.fields["website_logo"].widget = forms.URLInput(attrs={"class": "form-control", "placeholder": "https://…"})
        self.fields["business_contact_email"].widget = forms.EmailInput(attrs={"class": "form-control"})
        self.fields["pr_marketing_contact_email"].widget = forms.EmailInput(attrs={"class": "form-control"})
        self.fields["technical_contact_email"].widget = forms.EmailInput(attrs={"class": "form-control"})

        if not self.can_select_representatives:
            # Representative is defaulted to the creator; only membership admins can change.
            del self.fields["representative"]
        else:
            # Select2 uses AJAX, so only include currently-selected value as a choice.
            current = ""
            if self.is_bound:
                current = str(self.data.get("representative") or "").strip()
            else:
                initial = self.initial.get("representative")
                current = str(initial or "").strip()
            self.fields["representative"].choices = [user_choice_from_freeipa(current)] if current else []

    def clean_representative(self) -> str:
        if "representative" not in self.fields:
            return ""

        username = str(self.cleaned_data.get("representative") or "").strip()

        if not username:
            return ""

        if FreeIPAUser.get(username) is None:
            raise forms.ValidationError(
                f"Unknown user: {username}",
                code="unknown_representative",
            )

        return username




def organizations(request: HttpRequest) -> HttpResponse:
    username = str(request.user.get_username() or "").strip()
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
        orgs = Organization.objects.select_related("membership_level").all().order_by("name", "id")
        empty_label = "No organizations found."
    else:
        orgs = (
            Organization.objects.select_related("membership_level")
            .filter(representative=username)
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
    username = str(request.user.get_username() or "").strip()
    if not username:
        raise Http404

    can_select_representatives = request.user.has_perm(ASTRA_ADD_MEMBERSHIP)

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
                organization.representative = selected or username
            else:
                organization.representative = username

            organization.save()
            messages.success(request, "Organization created.")
            return redirect("organizations")
    else:
        form = OrganizationEditForm(
            can_select_representatives=can_select_representatives,
        )
        if can_select_representatives and "representative" in form.fields:
            form.fields["representative"].widget.attrs["data-ajax-url"] = reverse(
                "organization-representatives-search"
            )

    return render(
        request,
        "core/organization_form.html",
        {
            "form": form,
            "cancel_url": reverse("organizations"),
            "is_create": True,
            "organization": None,
            "show_representatives": "representative" in form.fields,
        },
    )


@require_GET
@json_permission_required_any({ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP})
def organization_representatives_search(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    if not q:
        return JsonResponse({"results": []})

    q_lower = q.lower()

    results: list[dict[str, str]] = []
    for u in FreeIPAUser.all():
        if not u.username:
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

    username = str(request.user.get_username() or "").strip()
    is_representative = bool(username and username == organization.representative)

    representative_username = _normalize_str(organization.representative)
    representative_full_name = ""
    if representative_username:
        representative_user = FreeIPAUser.get(representative_username)
        if representative_user is not None:
            representative_full_name = representative_user.full_name

    sponsorship: OrganizationSponsorship | None = OrganizationSponsorship.objects.select_related("membership_type").filter(
        organization=organization
    ).first()
    now = timezone.now()
    expiring_soon_by = now + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS)
    sponsorship_is_expiring_soon = bool(sponsorship and sponsorship.expires_at and sponsorship.expires_at <= expiring_soon_by)

    pending_membership_level_request = (
        MembershipRequest.objects.select_related("membership_type")
        .filter(
            requested_organization=organization,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
        .order_by("-requested_at")
        .first()
    )

    sponsorship_request_id: int | None = None
    if organization.membership_level_id:
        approved_log = (
            MembershipLog.objects.filter(
                target_organization=organization,
                membership_request__isnull=False,
                action=MembershipLog.Action.approved,
            )
            .only("membership_request_id", "created_at")
            .order_by("-created_at", "-pk")
            .first()
        )
        if approved_log is not None and approved_log.membership_request_id is not None:
            sponsorship_request_id = int(approved_log.membership_request_id)
        else:
            approved_req = (
                MembershipRequest.objects.filter(
                    requested_organization=organization,
                    membership_type_id=organization.membership_level_id,
                    status=MembershipRequest.Status.approved,
                )
                .only("pk", "decided_at", "requested_at")
                .order_by("-decided_at", "-requested_at")
                .first()
            )
            if approved_req is not None:
                sponsorship_request_id = int(approved_req.pk)

    can_edit_organization = _can_edit_organization(request, organization)

    return render(
        request,
        "core/organization_detail.html",
        {
            "organization": organization,
            "representative_username": representative_username,
            "representative_full_name": representative_full_name,
            "pending_membership_level_request": pending_membership_level_request,
            "sponsorship": sponsorship,
            "sponsorship_is_expiring_soon": sponsorship_is_expiring_soon,
            "sponsorship_request_id": sponsorship_request_id,
            "is_representative": is_representative,
            "can_edit_organization": can_edit_organization,
        },
    )


def organization_sponsorship_extend(request: HttpRequest, organization_id: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    organization = get_object_or_404(Organization, pk=organization_id)
    _require_representative(request, organization)

    if organization.membership_level_id is None:
        messages.error(request, "No sponsorship level set to extend.")
        return redirect("organization-detail", organization_id=organization.pk)

    membership_type = organization.membership_level
    if membership_type is None:
        messages.error(request, "No sponsorship level set to extend.")
        return redirect("organization-detail", organization_id=organization.pk)

    sponsorship = OrganizationSponsorship.objects.filter(organization=organization).first()
    if sponsorship is None or sponsorship.expires_at is None:
        messages.error(request, "No sponsorship expiration recorded to extend.")
        return redirect("organization-detail", organization_id=organization.pk)

    now = timezone.now()
    if sponsorship.expires_at <= now:
        messages.error(request, "This sponsorship has already expired and cannot be extended. Submit a new sponsorship request.")
        return redirect("organization-detail", organization_id=organization.pk)

    expiring_soon_by = now + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS)
    if sponsorship.expires_at > expiring_soon_by:
        messages.info(request, "This sponsorship is not expiring soon yet.")
        return redirect("organization-detail", organization_id=organization.pk)

    existing = (
        MembershipRequest.objects.filter(
            requested_organization=organization,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
        .order_by("-requested_at")
        .first()
    )
    if existing is not None:
        messages.info(request, "A sponsorship request is already pending.")
        return redirect("organization-detail", organization_id=organization.pk)

    responses: list[dict[str, str]] = []
    if organization.additional_information.strip():
        responses.append({"Additional Information": organization.additional_information.strip()})

    mr = MembershipRequest.objects.create(
        requested_username="",
        requested_organization=organization,
        membership_type=membership_type,
        status=MembershipRequest.Status.pending,
        responses=responses,
    )
    record_membership_request_created(
        membership_request=mr,
        actor_username=str(request.user.get_username() or "").strip(),
        send_submitted_email=False,
    )
    messages.success(request, "Sponsorship renewal request submitted for review.")
    return redirect("organization-detail", organization_id=organization.pk)


def organization_edit(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_organization_edit_access(request, organization)

    original_membership_level_id = organization.membership_level_id

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
        form.fields["representative"].widget.attrs["data-ajax-url"] = reverse("organization-representatives-search")

    if request.method == "POST" and form.is_valid():
        updated_org = form.save(commit=False)

        requested_membership_level: MembershipType | None = updated_org.membership_level

        if can_select_representatives and "representative" in form.fields:
            representative = form.cleaned_data.get("representative") or ""
            if not representative:
                form.add_error("representative", "A representative is required.")
                return render(
                    request,
                    "core/organization_form.html",
                    {
                        "organization": organization,
                        "form": form,
                        "is_create": False,
                        "cancel_url": "",
                        "show_representatives": True,
                    },
                )

            old_representative = organization.representative
            updated_org.representative = representative

            # If the org currently has an active sponsorship, keep FreeIPA group membership
            # aligned with the current representative.
            group_cn = ""
            if original_membership_level_id:
                current_level = MembershipType.objects.filter(pk=original_membership_level_id).only("group_cn").first()
                group_cn = str(current_level.group_cn or "").strip() if current_level is not None else ""

            if group_cn and old_representative and old_representative != representative:
                sponsorship = OrganizationSponsorship.objects.select_related("membership_type").filter(
                    organization=organization,
                    membership_type_id=original_membership_level_id,
                ).first()
                now = timezone.now()
                sponsorship_active = bool(sponsorship and sponsorship.expires_at and sponsorship.expires_at > now)

                if sponsorship_active:
                    old_rep = FreeIPAUser.get(old_representative)
                    new_rep = FreeIPAUser.get(representative)
                    if new_rep is None:
                        # This should already be prevented by OrganizationEditForm.clean_representative,
                        # but keep the view defensive.
                        form.add_error("representative", f"Unknown user: {representative}")
                        return render(
                            request,
                            "core/organization_form.html",
                            {
                                "organization": organization,
                                "form": form,
                                "is_create": False,
                                "cancel_url": "",
                                "show_representatives": True,
                            },
                        )

                    try:
                        if old_rep is not None:
                            old_rep.remove_from_group(group_name=group_cn)
                        if group_cn not in new_rep.groups_list:
                            new_rep.add_to_group(group_name=group_cn)
                    except Exception:
                        logger.exception(
                            "organization_edit: failed to sync representative group org_id=%s old=%r new=%r group_cn=%r",
                            organization.pk,
                            old_representative,
                            representative,
                            group_cn,
                        )

                        # Best-effort rollback: if we removed the old rep, try restoring it.
                        try:
                            if old_rep is not None and group_cn not in old_rep.groups_list:
                                old_rep.add_to_group(group_name=group_cn)
                        except Exception:
                            logger.exception(
                                "organization_edit: failed to rollback representative group org_id=%s old=%r group_cn=%r",
                                organization.pk,
                                old_representative,
                                group_cn,
                            )

                        form.add_error(None, "Failed to update FreeIPA group membership for the representative.")
                        return render(
                            request,
                            "core/organization_form.html",
                            {
                                "organization": organization,
                                "form": form,
                                "is_create": False,
                                "cancel_url": "",
                                "show_representatives": True,
                            },
                        )

        # Membership level changes are reviewed by the committee; do not apply directly.
        updated_org.membership_level_id = original_membership_level_id
        updated_org.save()

        if (
            requested_membership_level is not None
            and requested_membership_level.code != original_membership_level_id
        ):
            existing = (
                MembershipRequest.objects.filter(
                    requested_organization=organization,
                    status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                )
                .order_by("-requested_at")
                .first()
            )
            if existing is None:
                responses: list[dict[str, str]] = []
                if updated_org.additional_information.strip():
                    responses.append({"Additional Information": updated_org.additional_information.strip()})

                mr = MembershipRequest.objects.create(
                    requested_username="",
                    requested_organization=organization,
                    membership_type=requested_membership_level,
                    status=MembershipRequest.Status.pending,
                    responses=responses,
                )

                record_membership_request_created(
                    membership_request=mr,
                    actor_username=str(request.user.get_username() or "").strip(),
                    send_submitted_email=False,
                )

                messages.success(request, "Sponsorship level change submitted for review.")
            else:
                messages.info(request, "A sponsorship request is already pending.")

        return redirect("organization-detail", organization_id=organization.pk)

    return render(
        request,
        "core/organization_form.html",
        {
            "organization": organization,
            "form": form,
            "is_create": False,
            "cancel_url": "",
            "show_representatives": "representative" in form.fields,
        },
    )
