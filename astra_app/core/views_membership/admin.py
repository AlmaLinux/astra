import datetime
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from core.forms_membership import MembershipUpdateExpiryForm
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.membership import revoke_membership_group_access
from core.membership_constants import MembershipCategoryCode
from core.membership_dues import change_membership_dues, effective_dues_usd, parse_dues_usd, payable_dues_usd
from core.models import Membership, MembershipLog
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
    membership_minutes,
    membership_minutes_api,
    membership_stats,
    stats_membership_summary_api,
)
from core.views_utils import (
    _normalize_str,
    _resolve_post_redirect,
    get_username,
    parse_datatables_request_base,
    post_only_404,
    typed_confirmation_matches,
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
        users_by_username = {user.username: user for user in all_freeipa_users if user.username}
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

        if representative.full_name:
            representative_full_names[username] = representative.full_name

    return representative_full_names


def _build_membership_sponsor_rows(*, q: str) -> list[dict[str, object]]:
    active_sponsorships = _load_active_sponsorship_memberships()
    representative_usernames = {
        membership.target_organization.representative
        for membership in active_sponsorships
        if membership.target_organization is not None and membership.target_organization.representative
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

        rep_username = organization.representative
        rep_fullname = representative_full_names.get(rep_username, "")

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


def _load_committee_membership(request: HttpRequest, membership_id: int) -> tuple[Membership, str] | HttpResponse:
    """The active membership a committee action targets, and where to send the committee afterwards."""
    membership = (
        Membership.objects.active()
        .select_related("membership_type", "target_organization")
        .filter(pk=membership_id)
        .first()
    )
    if membership is None:
        # Every sponsorship change replaces the membership row (see Membership.replace_within_category),
        # so a page loaded before another change posts an id that no longer exists.
        messages.error(request, "That membership has changed or expired since the page was loaded.")
        return redirect(_resolve_post_redirect(request, default=reverse("users"), use_referer=True))

    if membership.target_organization is not None:
        default_redirect = reverse("organization-detail", kwargs={"organization_id": membership.target_organization.pk})
    else:
        default_redirect = reverse("user-profile", kwargs={"username": membership.target_username})
    return membership, _resolve_post_redirect(request, default=default_redirect, use_referer=True)


def _parse_expiry_datetime_or_redirect(
    request: HttpRequest,
    *,
    redirect_to: str,
) -> tuple[datetime.datetime, str] | HttpResponse:
    """The new end-of-day UTC expiry and the committee's optional reason."""
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
    expires_at = datetime.datetime.combine(expires_on, datetime.time(23, 59, 59), tzinfo=datetime.UTC)
    return expires_at, form.cleaned_data["expiration_reason"]




@permission_required(ASTRA_CHANGE_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_update(request: HttpRequest, membership_id: int) -> HttpResponse:
    """Save the manage membership dialog: the expiry and, for sponsorships on paid tiers, the dues.

    Only the fields that changed are recorded, all or nothing, each with its own reason
    as its committee note.
    """
    result = _load_committee_membership(request, membership_id)
    if isinstance(result, HttpResponse):
        return result
    membership, redirect_to = result
    is_sponsorship = membership.target_organization is not None

    parsed = _parse_expiry_datetime_or_redirect(request, redirect_to=redirect_to)
    if isinstance(parsed, HttpResponse):
        return parsed
    expires_at, expiration_reason = parsed
    actor_username = get_username(request)

    changed = False
    try:
        with transaction.atomic():
            # The dialog only has a dues field for sponsorships on paid tiers.
            if is_sponsorship and "dues_usd" in request.POST:
                dues_usd = payable_dues_usd(membership.membership_type, parse_dues_usd(request.POST["dues_usd"]))
                if dues_usd != effective_dues_usd(membership):
                    # Before the expiry change, which replaces membership's term.
                    change_membership_dues(
                        membership=membership,
                        dues_usd=dues_usd,
                        reason=str(request.POST.get("dues_reason") or ""),
                        actor_username=actor_username,
                    )
                    changed = True
            # The committee edits a date, so an unchanged date keeps the stored timestamp.
            if expires_at.date() != membership.expires_at.astimezone(datetime.UTC).date():
                MembershipLog.create_for_expiry_change(
                    actor_username=actor_username,
                    membership_type=membership.membership_type,
                    expires_at=expires_at,
                    reason=expiration_reason,
                    target_username=membership.target_username,
                    target_organization=membership.target_organization,
                )
                changed = True
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect(redirect_to)

    if changed:
        messages.success(request, "Sponsorship updated." if is_sponsorship else "Membership updated.")
    else:
        messages.info(request, "No changes to save.")
    return redirect(redirect_to)


@permission_required(ASTRA_DELETE_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def membership_terminate(request: HttpRequest, membership_id: int) -> HttpResponse:
    result = _load_committee_membership(request, membership_id)
    if isinstance(result, HttpResponse):
        return result
    membership, resolved_redirect = result
    organization = membership.target_organization

    if organization is not None:
        confirmed = typed_confirmation_matches(request.POST.get("confirm"), organization.name)
        confirmation_error = "Type the organization name to confirm the termination."
    else:
        confirmed = typed_confirmation_matches(request.POST.get("confirm"), membership.target_username)
        confirmation_error = "Type the username to confirm the termination."
    if not confirmed:
        messages.error(request, confirmation_error)
        return redirect(resolved_redirect)

    # Revoke access first: a termination recorded while FreeIPA still grants it would be a lie.
    if not revoke_membership_group_access(membership):
        messages.error(request, "Failed to remove the FreeIPA group access, so the membership was not terminated.")
        return redirect(resolved_redirect)

    MembershipLog.create_for_termination(
        actor_username=get_username(request),
        membership_type=membership.membership_type,
        target_username=membership.target_username,
        target_organization=organization,
    )
    messages.success(request, "Sponsorship terminated." if organization is not None else "Membership terminated.")
    return redirect(resolved_redirect)

__all__ = [
    "membership_audit_log",
    "membership_audit_log_api",
    "membership_audit_log_organization",
    "membership_audit_log_user",
    "membership_minutes",
    "membership_minutes_api",
    "membership_sponsors_api",
    "membership_update",
    "membership_sponsors_list",
    "membership_stats",
    "stats_membership_summary_api",
    "membership_terminate",
]
