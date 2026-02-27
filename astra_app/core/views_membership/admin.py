import datetime
import logging

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy

from core.forms_membership import MembershipUpdateExpiryForm
from core.freeipa.user import FreeIPAUser
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    get_valid_memberships,
    remove_organization_representative_from_group_if_present,
    remove_user_from_group,
)
from core.models import Membership, MembershipLog, MembershipType, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP
from core.views_membership_admin import (
    membership_audit_log,
    membership_audit_log_organization,
    membership_audit_log_user,
    membership_sponsors_list,
    membership_stats,
    membership_stats_data,
)
from core.views_utils import _normalize_str, _resolve_post_redirect, get_username, post_only_404

logger = logging.getLogger(__name__)


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
        if "expires_on" in form.errors:
            for error in form.errors["expires_on"]:
                messages.error(request, str(error))
        else:
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

        MembershipLog.create_for_expiry_change(
            actor_username=get_username(request),
            membership_type=membership_type,
            expires_at=expires_at,
            target_organization=organization,
        )

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

    MembershipLog.create_for_expiry_change(
        actor_username=get_username(request),
        membership_type=membership_type,
        expires_at=expires_at,
        target_username=username,
    )

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

__all__ = [
    "_load_active_membership",
    "membership_audit_log",
    "membership_audit_log_organization",
    "membership_audit_log_user",
    "membership_set_expiry",
    "membership_sponsors_list",
    "membership_stats",
    "membership_stats_data",
    "membership_terminate",
]
