import datetime
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from core.forms_membership import MembershipUpdateExpiryForm
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    get_valid_memberships,
    remove_organization_representative_from_group_if_present,
    remove_user_from_group,
)
from core.membership_constants import MembershipCategoryCode
from core.models import Membership, MembershipLog, MembershipType, Organization
from core.permissions import (
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    MEMBERSHIP_PERMISSIONS,
    has_any_membership_permission,
    json_permission_required_any,
)
from core.views_membership_admin import (
    membership_audit_log,
    membership_audit_log_api,
    membership_audit_log_organization,
    membership_audit_log_user,
    membership_stats,
    stats_membership_composition_charts_api,
    stats_membership_retention_chart_api,
    stats_membership_summary_api,
    stats_membership_trends_charts_api,
)
from core.views_utils import (
    _normalize_str,
    _resolve_post_redirect,
    get_username,
    normalize_freeipa_username,
    parse_datatables_request_base,
    post_only_404,
)

logger = logging.getLogger(__name__)


def _load_active_sponsorship_memberships() -> list[Membership]:
    return list(
        Membership.objects.active()
        .filter(membership_type__category_id=MembershipCategoryCode.sponsorship)
        .select_related("target_organization", "membership_type")
        .order_by("expires_at")
    )


def _load_representative_full_names(usernames: set[str]) -> dict[str, str]:
    representative_full_names: dict[str, str] = {}
    usernames_to_lookup = sorted(usernames)
    users_by_username: dict[str, FreeIPAUser] = {}
    use_bulk_lookup = len(usernames_to_lookup) > 1
    if use_bulk_lookup:
        all_freeipa_users = FreeIPAUser.all()
        users_by_username = {
            username: user
            for user in all_freeipa_users
            for username in [str(user.username or "").strip()]
            if username
        }
        if not users_by_username:
            # FreeIPAUser.all() can return [] when the backend is unavailable.
            # Fall back to per-user lookups to preserve existing behavior.
            use_bulk_lookup = False

    for username in usernames_to_lookup:
        representative: FreeIPAUser | None = None
        if use_bulk_lookup:
            representative = users_by_username.get(username)
        else:
            try:
                representative = FreeIPAUser.get(username)
            except Exception:
                logger.exception(
                    "membership_sponsors: failed to fetch representative from FreeIPA username=%s",
                    username,
                    extra=current_exception_log_fields(),
                )
                continue

        if representative is None:
            continue

        full_name = str(representative.full_name or "").strip()
        if full_name:
            representative_full_names[username] = full_name

    return representative_full_names


def _build_membership_sponsor_rows(*, q: str) -> list[dict[str, object]]:
    active_sponsorships = _load_active_sponsorship_memberships()
    representative_usernames = {
        str(membership.target_organization.representative or "").strip()
        for membership in active_sponsorships
        if membership.target_organization is not None and str(membership.target_organization.representative or "").strip()
    }
    representative_full_names = _load_representative_full_names(representative_usernames)

    now = timezone.now()
    warning_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
    normalized_query = q.strip().lower()
    rows: list[dict[str, object]] = []
    for membership in active_sponsorships:
        organization = membership.target_organization
        if organization is None:
            continue

        rep_username = str(organization.representative or "").strip()
        rep_fullname = representative_full_names.get(rep_username, "")

        days_left: int | None = None
        is_expiring_soon = False
        expires_display = "-"
        expires_at_order = "9999-12-31"
        if membership.expires_at is not None:
            days_left = (membership.expires_at - now).days
            is_expiring_soon = days_left <= warning_days
            expires_display = f"{membership.expires_at:%Y-%m-%d} ({days_left} days left)"
            expires_at_order = membership.expires_at.strftime("%Y-%m-%d %H:%M:%S")

        representative_label = rep_fullname and f"{rep_fullname} ({rep_username})" or rep_username

        row = {
            "membership_id": membership.pk,
            "organization": {
                "id": organization.pk,
                "name": organization.name,
            },
            "representative": {
                "username": rep_username,
                "full_name": rep_fullname,
                "display_label": representative_label,
            },
            "sponsorship_level": membership.membership_type.name,
            "days_left": days_left,
            "is_expiring_soon": is_expiring_soon,
            "expires_display": expires_display,
            "expires_at_order": expires_at_order,
        }
        if normalized_query:
            search_blob = " ".join(
                (
                    organization.name,
                    rep_username,
                    rep_fullname,
                    membership.membership_type.name,
                    expires_display,
                )
            ).lower()
            if normalized_query not in search_blob:
                continue
        rows.append(row)

    return rows


def _parse_membership_sponsors_datatables_request(request: HttpRequest) -> tuple[int, int, int, str]:
    draw, start, length = parse_datatables_request_base(
        request,
        additional_allowed_params={"q"},
        allow_cache_buster=True,
    )

    order_column = _normalize_str(request.GET.get("order[0][column]"))
    order_dir = _normalize_str(request.GET.get("order[0][dir]")).lower()
    order_name = _normalize_str(request.GET.get("order[0][name]"))
    column_data = _normalize_str(request.GET.get("columns[0][data]"))
    column_name = _normalize_str(request.GET.get("columns[0][name]"))
    column_searchable = _normalize_str(request.GET.get("columns[0][searchable]"))
    column_orderable = _normalize_str(request.GET.get("columns[0][orderable]"))
    if (
        order_column != "0"
        or order_dir != "asc"
        or order_name != "expires_at"
        or column_data != "membership_id"
        or column_name != "expires_at"
        or column_searchable.lower() != "true"
        or column_orderable.lower() != "true"
    ):
        raise ValueError("Invalid query parameters.")

    q = _normalize_str(request.GET.get("q"))
    return draw, start, length, q


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def membership_sponsors_api(request: HttpRequest) -> JsonResponse:
    try:
        draw, start, length, q = _parse_membership_sponsors_datatables_request(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    all_rows = _build_membership_sponsor_rows(q="")
    rows = _build_membership_sponsor_rows(q=q) if q else all_rows
    sliced_rows = rows[start : start + length]
    return JsonResponse(
        {
            "draw": draw,
            "recordsTotal": len(all_rows),
            "recordsFiltered": len(rows),
            "data": sliced_rows,
        }
    )


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_sponsors_list(request: HttpRequest) -> HttpResponse:
    initial_q = _normalize_str(request.GET.get("q"))
    return render(
        request,
        "core/sponsorship_list.html",
        {
            "initial_q": initial_q,
            "organization_detail_url_template": reverse("organization-detail", args=[123456789]).replace(
                "123456789", "__organization_id__"
            ),
            "user_profile_url_template": reverse("user-profile", args=["__username__"]),
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
    username = normalize_freeipa_username(username)
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
    "membership_audit_log_api",
    "membership_audit_log_organization",
    "membership_audit_log_user",
    "membership_sponsors_api",
    "membership_set_expiry",
    "membership_sponsors_list",
    "membership_stats",
    "stats_membership_composition_charts_api",
    "stats_membership_retention_chart_api",
    "stats_membership_summary_api",
    "stats_membership_trends_charts_api",
    "membership_terminate",
]
