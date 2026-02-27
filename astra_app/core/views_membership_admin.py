import datetime
import logging

from django.conf import settings
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.core.cache import cache
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from core.country_codes import country_code_status_from_user_data
from core.freeipa.user import FreeIPAUser
from core.membership_constants import MembershipCategoryCode
from core.models import Membership, MembershipLog, MembershipRequest
from core.permissions import (
    ASTRA_VIEW_MEMBERSHIP,
    MEMBERSHIP_PERMISSIONS,
    has_any_membership_permission,
    json_permission_required_any,
)
from core.views_utils import _normalize_str, build_page_url_prefix, paginate_and_build_context

logger = logging.getLogger(__name__)


def _paginate_and_render_audit_log(
    request: HttpRequest,
    *,
    logs,
    q: str,
    extra_query_params: dict[str, str],
    filter_context: dict[str, str],
) -> HttpResponse:
    page_number = _normalize_str(request.GET.get("page")) or None
    query_params: dict[str, str] = {}
    if q:
        query_params["q"] = q
    query_params.update(extra_query_params)
    _, page_url_prefix = build_page_url_prefix(query_params, page_param="page")
    page_ctx = paginate_and_build_context(logs, page_number, 50, page_url_prefix=page_url_prefix)
    return render(
        request,
        "core/membership_audit_log.html",
        {
            "logs": page_ctx["page_obj"].object_list,
            "q": q,
            **filter_context,
            **page_ctx,
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    username = _normalize_str(request.GET.get("username"))
    raw_org = _normalize_str(request.GET.get("organization"))
    organization_id = int(raw_org) if raw_org.isdigit() else None

    logs = MembershipLog.objects.select_related(
        "membership_type",
        "membership_request",
        "membership_request__membership_type",
        "target_organization",
    ).all()
    if username:
        logs = logs.filter(target_username=username)
    if organization_id is not None:
        logs = logs.for_organization_identifier(organization_id)
    if q:
        logs = logs.filter(
            Q(target_username__icontains=q)
            | Q(target_organization__name__icontains=q)
            | Q(target_organization_code__icontains=q)
            | Q(target_organization_name__icontains=q)
            | Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    logs = logs.order_by("-created_at")
    extra_query_params: dict[str, str] = {}
    if username:
        extra_query_params["username"] = username
    if organization_id is not None:
        extra_query_params["organization"] = str(organization_id)
    organization_str = str(organization_id) if organization_id is not None else ""
    return _paginate_and_render_audit_log(
        request,
        logs=logs,
        q=q,
        extra_query_params=extra_query_params,
        filter_context={
            "filter_username": username,
            "filter_username_param": username,
            "filter_organization": organization_str,
            "filter_organization_param": organization_str,
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_organization(request: HttpRequest, organization_id: int) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))

    logs = (
        MembershipLog.objects.select_related(
            "membership_type",
            "membership_request",
            "membership_request__membership_type",
            "target_organization",
        )
        .for_organization_identifier(organization_id)
        .order_by("-created_at")
    )
    if q:
        logs = logs.filter(
            Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    return _paginate_and_render_audit_log(
        request,
        logs=logs,
        q=q,
        extra_query_params={},
        filter_context={
            "filter_username": "",
            "filter_username_param": "",
            "filter_organization": str(organization_id),
            "filter_organization_param": str(organization_id),
        },
    )


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_user(request: HttpRequest, username: str) -> HttpResponse:
    username = _normalize_str(username)
    q = _normalize_str(request.GET.get("q"))

    logs = (
        MembershipLog.objects.select_related(
            "membership_type",
            "membership_request",
            "membership_request__membership_type",
        )
        .filter(target_username=username)
        .order_by("-created_at")
    )
    if q:
        logs = logs.filter(
            Q(actor_username__icontains=q)
            | Q(membership_type__name__icontains=q)
            | Q(membership_type__code__icontains=q)
            | Q(action__icontains=q)
        )

    return _paginate_and_render_audit_log(
        request,
        logs=logs,
        q=q,
        extra_query_params={},
        filter_context={
            "filter_username": username,
            "filter_username_param": "",
        },
    )


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_stats(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/membership_stats.html",
        {
            "stats_data_url": reverse("membership-stats-data"),
        },
    )


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_sponsors_list(request: HttpRequest) -> HttpResponse:
    active_sponsorships = list(
        Membership.objects.active()
        .filter(category_id=MembershipCategoryCode.sponsorship)
        .select_related("target_organization", "membership_type")
        .order_by("expires_at")
    )

    representative_usernames = {
        str(membership.target_organization.representative or "").strip()
        for membership in active_sponsorships
        if membership.target_organization is not None and str(membership.target_organization.representative or "").strip()
    }

    representative_full_names: dict[str, str] = {}
    for username in sorted(representative_usernames):
        try:
            representative = FreeIPAUser.get(username)
        except Exception:
            logger.exception(
                "membership_sponsors_list: failed to fetch representative from FreeIPA username=%s",
                username,
            )
            continue

        if representative is None:
            continue

        full_name = str(representative.full_name or "").strip()
        if full_name:
            representative_full_names[username] = full_name

    now = timezone.now()
    warning_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
    memberships: list[dict[str, object]] = []
    for membership in active_sponsorships:
        organization = membership.target_organization
        if organization is None:
            continue

        rep_username = str(organization.representative or "").strip()
        rep_fullname = representative_full_names.get(rep_username, "")

        days_left: int | None = None
        is_expiring_soon = False
        if membership.expires_at is not None:
            days_left = (membership.expires_at - now).days
            is_expiring_soon = days_left <= warning_days

        memberships.append(
            {
                "organization": organization,
                "membership": membership,
                "rep_username": rep_username,
                "rep_fullname": rep_fullname,
                "days_left": days_left,
                "is_expiring_soon": is_expiring_soon,
            }
        )

    return render(
        request,
        "core/sponsorship_list.html",
        {
            "memberships": memberships,
        },
    )


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def membership_stats_data(_request: HttpRequest) -> HttpResponse:
    now = timezone.now()

    def compute_payload() -> dict[str, object]:
        all_freeipa_users = FreeIPAUser.all()
        users_by_username = {u.username: u for u in all_freeipa_users if u.username}
        active_freeipa_users = [u for u in all_freeipa_users if u.is_active]

        active_memberships = Membership.objects.active()

        summary: dict[str, int] = {
            "total_freeipa_users": len(all_freeipa_users),
            "active_individual_memberships": active_memberships.filter(
                membership_type__category__is_individual=True
            )
            .values("target_username")
            .distinct()
            .count(),
            "pending_requests": MembershipRequest.objects.filter(
                status=MembershipRequest.Status.pending
            ).count(),
            "on_hold_requests": MembershipRequest.objects.filter(
                status=MembershipRequest.Status.on_hold
            ).count(),
            "expiring_soon_90_days": active_memberships.filter(expires_at__lte=now + datetime.timedelta(days=90))
            .exclude(expires_at__isnull=True)
            .count(),
            "active_org_sponsorships": Membership.objects.active()
            .filter(target_organization__isnull=False)
            .count(),
        }

        membership_type_rows = (
            active_memberships.values("membership_type_id", "membership_type__name")
            .annotate(count=Count("id"))
            .order_by("membership_type__name")
        )
        membership_type_labels: list[str] = [r["membership_type__name"] for r in membership_type_rows]
        membership_type_counts: list[int] = [int(r["count"]) for r in membership_type_rows]

        start = now - datetime.timedelta(days=365)
        request_rows = (
            MembershipRequest.objects.filter(requested_at__gte=start)
            .annotate(period=TruncMonth("requested_at"))
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        )
        requests_labels = [timezone.localtime(r["period"]).strftime("%Y-%m") for r in request_rows if r["period"]]
        requests_counts = [int(r["count"]) for r in request_rows]

        decision_statuses = [
            MembershipRequest.Status.approved,
            MembershipRequest.Status.rejected,
            MembershipRequest.Status.ignored,
            MembershipRequest.Status.rescinded,
        ]
        decision_rows = (
            MembershipRequest.objects.filter(decided_at__isnull=False, decided_at__gte=start)
            .filter(status__in=decision_statuses)
            .annotate(period=TruncMonth("decided_at"))
            .values("period", "status")
            .annotate(count=Count("id"))
            .order_by("period", "status")
        )
        decision_periods = sorted({r["period"] for r in decision_rows if r["period"]})
        decision_labels = [timezone.localtime(p).strftime("%Y-%m") for p in decision_periods]
        decision_index = {(r["period"], r["status"]): int(r["count"]) for r in decision_rows}
        decision_datasets: list[dict[str, object]] = []
        for status in decision_statuses:
            decision_datasets.append(
                {
                    "label": str(status),
                    "data": [decision_index.get((p, status), 0) for p in decision_periods],
                }
            )

        exp_rows = (
            Membership.objects.filter(expires_at__isnull=False, expires_at__gte=now, expires_at__lte=now + datetime.timedelta(days=365))
            .annotate(period=TruncMonth("expires_at"))
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        )

        def _next_month(period: datetime.datetime) -> datetime.datetime:
            year = period.year
            month = period.month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
            return period.replace(year=year, month=month, day=1)

        exp_index = {r["period"]: int(r["count"]) for r in exp_rows if r["period"]}
        exp_periods = sorted(exp_index)
        exp_labels: list[str] = []
        exp_counts: list[int] = []
        if exp_periods:
            current_local = timezone.localtime(now)
            current = current_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = exp_periods[-1]
            while current <= end:
                exp_labels.append(timezone.localtime(current).strftime("%Y-%m"))
                exp_counts.append(exp_index.get(current, 0))
                current = _next_month(current)

        charts: dict[str, object] = {
            "membership_types": {
                "labels": membership_type_labels,
                "counts": membership_type_counts,
            },
            "requests_trend": {
                "labels": requests_labels,
                "counts": requests_counts,
            },
            "decisions_trend": {
                "labels": decision_labels,
                "datasets": decision_datasets,
            },
            "expirations_upcoming": {
                "labels": exp_labels,
                "counts": exp_counts,
            },
        }

        def nationality_distribution(users: list[FreeIPAUser]) -> dict[str, list[object]]:
            counts: dict[str, int] = {}
            for user in users:
                status = country_code_status_from_user_data(user._user_data)
                if not status.is_valid or not status.code:
                    code = "Unknown/Unset"
                else:
                    code = status.code
                counts[code] = counts.get(code, 0) + 1

            ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            return {
                "labels": [k for k, _v in ordered],
                "counts": [v for _k, v in ordered],
            }

        active_member_usernames = set(active_memberships.values_list("target_username", flat=True).distinct())
        active_member_users: list[FreeIPAUser] = []
        for username in sorted(active_member_usernames):
            user = users_by_username.get(username)
            if user is None:
                user = FreeIPAUser.get(username)
            if user is not None and user.is_active:
                active_member_users.append(user)

        charts["nationality_all_users"] = nationality_distribution(active_freeipa_users)
        charts["nationality_active_members"] = nationality_distribution(active_member_users)

        return {
            "generated_at": timezone.localtime(now).isoformat(),
            "summary": summary,
            "charts": charts,
        }

    payload = cache.get_or_set("membership_stats:data:v4", compute_payload, timeout=300)
    return JsonResponse(payload)
