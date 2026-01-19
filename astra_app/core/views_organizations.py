from __future__ import annotations

import datetime
import logging
from typing import override

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.backends import FreeIPAUser
from core.membership_notes import add_note
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

    if any(
        request.user.has_perm(p)
        for p in (
            ASTRA_VIEW_MEMBERSHIP,
            ASTRA_ADD_MEMBERSHIP,
            ASTRA_CHANGE_MEMBERSHIP,
            ASTRA_DELETE_MEMBERSHIP,
        )
    ):
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
        help_text="Select the user who will be the organization's representative.",
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
            "name",
            "website_logo",
            "website",
            "logo",
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
            "name": "Organization name",
            "website_logo": "Website logo (URL)",
            "website": "Website URL",
            "logo": "Accounts logo (upload)",
        }

        help_texts = {
            "name": "This is the name we will display publicly for sponsor recognition.",
            "website_logo": "Share a direct link to your logo file, or a link to your brand assets.",
            "website": "Enter the URL you want your logo to link to (homepage or a dedicated landing page).",
        }

    @override
    def __init__(self, *args, **kwargs):
        self.can_select_representatives: bool = bool(kwargs.pop("can_select_representatives", False))
        super().__init__(*args, **kwargs)

        self.fields["business_contact_name"].required = True
        self.fields["business_contact_email"].required = True
        self.fields["pr_marketing_contact_name"].required = True
        self.fields["pr_marketing_contact_email"].required = True
        self.fields["technical_contact_name"].required = True
        self.fields["technical_contact_email"].required = True
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

        # Avoid leaking which org they represent; just state the rule.
        conflict_exists = (
            Organization.objects.filter(representative=username)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if conflict_exists:
            raise forms.ValidationError(
                "That user is already the representative of another organization.",
                code="representative_not_unique",
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
            messages.success(request, "Organization created.")
            if organization.representative == username:
                return redirect("organization-sponsorship-manage", organization_id=organization.pk)

            messages.info(request, "Sponsorship requests must be submitted by the organization's representative.")
            return redirect("organization-detail", organization_id=organization.pk)
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

    membership_level_badge_text = ""
    if organization.membership_level_id:
        membership_level_badge_text = str(organization.membership_level_id).replace("_", " ").title()

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
            "membership_level_badge_text": membership_level_badge_text,
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


class OrganizationSponsorshipRequestForm(forms.Form):
    membership_level = forms.ModelChoiceField(
        queryset=MembershipType.objects.none(),
        required=True,
        label="Sponsorship level",
        empty_label="Select a sponsorship level…",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    additional_information = forms.CharField(
        required=False,
        label="Additional information",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )

    @override
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["membership_level"].queryset = MembershipType.objects.filter(isOrganization=True).order_by(
            "sort_order",
            "code",
        )


def organization_sponsorship_manage(request: HttpRequest, organization_id: int) -> HttpResponse:
    organization = get_object_or_404(Organization, pk=organization_id)
    _require_representative(request, organization)

    pending_membership_level_request = (
        MembershipRequest.objects.select_related("membership_type")
        .filter(
            requested_organization=organization,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
        .order_by("-requested_at")
        .first()
    )

    if request.method == "POST":
        form = OrganizationSponsorshipRequestForm(request.POST)

        if pending_membership_level_request is not None:
            messages.info(request, "A sponsorship request is already pending.")
            return redirect("organization-detail", organization_id=organization.pk)

        if not form.is_valid():
            messages.error(request, "Please correct the errors below.")
        else:
            requested_membership_level: MembershipType = form.cleaned_data["membership_level"]
            additional_information = str(form.cleaned_data.get("additional_information") or "").strip()

            responses: list[dict[str, str]] = []
            if additional_information:
                responses.append({"Additional Information": additional_information})

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

            if organization.membership_level_id and requested_membership_level.code != organization.membership_level_id:
                messages.success(request, "Sponsorship level change submitted for review.")
            else:
                messages.success(request, "Sponsorship request submitted for review.")
            return redirect("organization-detail", organization_id=organization.pk)
    else:
        initial: dict[str, object] = {}
        if organization.membership_level_id:
            initial["membership_level"] = organization.membership_level_id
        form = OrganizationSponsorshipRequestForm(initial=initial)

    return render(
        request,
        "core/organization_sponsorship_manage.html",
        {
            "organization": organization,
            "pending_membership_level_request": pending_membership_level_request,
            "form": form,
        },
    )


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
        form.fields["representative"].widget.attrs["data-ajax-url"] = (
            reverse("organization-representatives-search") + f"?organization_id={organization.pk}"
        )

    if request.method == "POST" and form.is_valid():
        updated_org = form.save(commit=False)

        old_representative = ""
        new_representative = ""
        group_cn = ""
        sponsorship_active = False
        synced_groups = False

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
            new_representative = representative
            updated_org.representative = representative

            # If the org currently has an active sponsorship, keep FreeIPA group membership
            # aligned with the current representative.
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
                        synced_groups = True
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

        try:
            updated_org.save()
        except IntegrityError:
            # Race-condition safety: DB is the source of truth.
            # Best-effort rollback if we already changed FreeIPA groups.
            if synced_groups and group_cn and old_representative and new_representative and old_representative != new_representative:
                try:
                    old_rep = FreeIPAUser.get(old_representative)
                    new_rep = FreeIPAUser.get(new_representative)
                    if new_rep is not None and group_cn in new_rep.groups_list:
                        new_rep.remove_from_group(group_name=group_cn)
                    if old_rep is not None and group_cn not in old_rep.groups_list:
                        old_rep.add_to_group(group_name=group_cn)
                except Exception:
                    logger.exception(
                        "organization_edit: failed to rollback representative group after IntegrityError org_id=%s old=%r new=%r group_cn=%r",
                        organization.pk,
                        old_representative,
                        new_representative,
                        group_cn,
                    )

            form.add_error(
                "representative",
                "That user is already the representative of another organization.",
            )
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

        if old_representative and new_representative and old_representative != new_representative:
            pending_requests = list(
                MembershipRequest.objects.select_related("membership_type").filter(
                    requested_organization=organization,
                    status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
                )
            )
            if pending_requests:
                actor_username = str(request.user.get_username() or "").strip()
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
