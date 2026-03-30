import datetime
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping

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
from core.logging_extras import current_exception_log_fields
from core.membership import visible_committee_membership_requests
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

MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET = "365"
MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS: Mapping[str, int | None] = {
    "30": 30,
    "90": 90,
    "180": 180,
    "365": 365,
    "all": None,
}


def _month_start_utc(value: datetime.datetime) -> datetime.datetime:
    value_utc = value.astimezone(datetime.UTC)
    return datetime.datetime(value_utc.year, value_utc.month, 1, tzinfo=datetime.UTC)


def _add_months_utc(value: datetime.datetime, months: int) -> datetime.datetime:
    month_index = value.month - 1 + months
    year = value.year + (month_index // 12)
    month = (month_index % 12) + 1
    return value.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _compute_retention_cohort_12m(
    *,
    now: datetime.datetime,
    cohort_limit: int,
) -> tuple[dict[str, int], dict[str, list[object]]]:
    events = list(
        MembershipLog.objects.filter(
            membership_type__category__is_individual=True,
            action__in=[MembershipLog.Action.approved, MembershipLog.Action.terminated],
        )
        .exclude(target_username="")
        .select_related("membership_type")
        .order_by("target_username", "created_at", "id")
    )

    events_by_username: dict[str, list[MembershipLog]] = defaultdict(list)
    for event in events:
        username = str(event.target_username).strip()
        if not username:
            continue
        events_by_username[username].append(event)

    now_utc = now.astimezone(datetime.UTC)
    cohorts: dict[str, dict[str, int]] = {}

    for username, user_events in events_by_username.items():
        approvals = [event for event in user_events if event.action == MembershipLog.Action.approved]
        if not approvals:
            continue
        termination_times = [
            event.created_at.astimezone(datetime.UTC)
            for event in user_events
            if event.action == MembershipLog.Action.terminated
        ]

        first_approval = approvals[0]
        first_approval_at = first_approval.created_at.astimezone(datetime.UTC)
        first_expires_at = first_approval.expires_at
        if first_expires_at is not None:
            first_expires_at = first_expires_at.astimezone(datetime.UTC)
        cohort_start = _month_start_utc(first_approval_at)
        horizon = _add_months_utc(cohort_start, 12)
        if horizon > now_utc:
            continue

        next_approval = next(
            (approval for approval in approvals[1:] if approval.created_at.astimezone(datetime.UTC) <= horizon),
            None,
        )

        cohort_label = cohort_start.strftime("%Y-%m")
        cohort_row = cohorts.setdefault(
            cohort_label,
            {
                "cohort_size": 0,
                "retained": 0,
                "lapsed_then_renewed": 0,
                "lapsed_not_renewed": 0,
            },
        )
        cohort_row["cohort_size"] += 1

        if next_approval is not None:
            next_approval_at = next_approval.created_at.astimezone(datetime.UTC)
            terminated_before_renewal = any(terminated_at <= next_approval_at for terminated_at in termination_times)
            if not terminated_before_renewal and first_expires_at is not None and next_approval_at <= first_expires_at:
                cohort_row["retained"] += 1
            else:
                cohort_row["lapsed_then_renewed"] += 1
            continue

        terminated_before_horizon = any(terminated_at <= horizon for terminated_at in termination_times)
        if not terminated_before_horizon and (first_expires_at is None or first_expires_at >= horizon):
            cohort_row["retained"] += 1
        else:
            cohort_row["lapsed_not_renewed"] += 1

    labels = sorted(cohorts)
    if cohort_limit > 0 and len(labels) > cohort_limit:
        labels = labels[-cohort_limit:]

    cohort_sizes = [cohorts[label]["cohort_size"] for label in labels]
    retained = [cohorts[label]["retained"] for label in labels]
    lapsed_then_renewed = [cohorts[label]["lapsed_then_renewed"] for label in labels]
    lapsed_not_renewed = [cohorts[label]["lapsed_not_renewed"] for label in labels]

    summary = {
        "cohorts": len(labels),
        "users": int(sum(cohort_sizes)),
        "retained": int(sum(retained)),
        "lapsed_then_renewed": int(sum(lapsed_then_renewed)),
        "lapsed_not_renewed": int(sum(lapsed_not_renewed)),
    }
    chart = {
        "labels": labels,
        "cohort_sizes": cohort_sizes,
        "retained": retained,
        "lapsed_then_renewed": lapsed_then_renewed,
        "lapsed_not_renewed": lapsed_not_renewed,
    }
    return summary, chart


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
    days = _normalize_str(request.GET.get("days")) or MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET
    if days not in MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS:
        days = MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET

    days_presets = [
        (key, "All time" if key == "all" else f"{key} days")
        for key in MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS
    ]

    stats_data_url = reverse("membership-stats-data")
    stats_data_url = f"{stats_data_url}?days={days}"

    return render(
        request,
        "core/membership_stats.html",
        {
            "stats_data_url": stats_data_url,
            "current_days": days,
            "days_presets": days_presets,
        },
    )


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_sponsors_list(request: HttpRequest) -> HttpResponse:
    active_sponsorships = list(
        Membership.objects.active()
        .filter(membership_type__category_id=MembershipCategoryCode.sponsorship)
        .select_related("target_organization", "membership_type")
        .order_by("expires_at")
    )

    representative_usernames = {
        str(membership.target_organization.representative or "").strip()
        for membership in active_sponsorships
        if membership.target_organization is not None and str(membership.target_organization.representative or "").strip()
    }

    representative_full_names: dict[str, str] = {}
    usernames_to_lookup = sorted(representative_usernames)
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
                    "membership_sponsors_list: failed to fetch representative from FreeIPA username=%s",
                    username,
                    extra=current_exception_log_fields(),
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
def membership_stats_data(request: HttpRequest) -> HttpResponse:
    if _normalize_str(request.GET.get("start")) or _normalize_str(request.GET.get("end")):
        return JsonResponse({"error": "Unsupported date-range parameter."}, status=400)

    days_param = _normalize_str(request.GET.get("days")) or MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET
    days_window = MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS.get(days_param)
    if days_param not in MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS:
        return JsonResponse({"error": "Invalid days parameter."}, status=400)

    now = timezone.now()
    trend_start = now - datetime.timedelta(days=days_window) if days_window is not None else None

    def compute_payload() -> dict[str, object]:
        # Statistics are aggregated, so profile privacy redaction would only
        # hide data needed for accurate counts.
        all_freeipa_users = FreeIPAUser.all(respect_privacy=False)
        users_by_username = {u.username: u for u in all_freeipa_users if u.username}
        active_freeipa_users = [u for u in all_freeipa_users if u.is_active]

        active_memberships = Membership.objects.active()
        active_individual_usernames = list(
            active_memberships.filter(membership_type__category__is_individual=True)
            .exclude(target_username="")
            .order_by()
            .values_list("target_username", flat=True)
            .distinct()
        )
        active_org_ids = list(
            active_memberships.filter(membership_type__category__is_organization=True)
            .filter(target_organization__isnull=False)
            .order_by()
            .values_list("target_organization_id", flat=True)
            .distinct()
        )

        summary: dict[str, object] = {
            "total_freeipa_users": len(all_freeipa_users),
            "active_individual_memberships": len(active_individual_usernames),
            "pending_requests": len(
                visible_committee_membership_requests(
                    MembershipRequest.objects.select_related("requested_organization")
                    .filter(status=MembershipRequest.Status.pending)
                    .order_by("requested_at", "pk"),
                    live_users_by_username=users_by_username,
                )
            ),
            "on_hold_requests": len(
                visible_committee_membership_requests(
                    MembershipRequest.objects.select_related("requested_organization")
                    .filter(status=MembershipRequest.Status.on_hold)
                    .order_by("on_hold_at", "requested_at", "pk"),
                    live_users_by_username=users_by_username,
                )
            ),
            "expiring_soon_90_days": active_memberships.filter(expires_at__lte=now + datetime.timedelta(days=90))
            .exclude(expires_at__isnull=True)
            .count(),
            "active_org_sponsorships": len(active_org_ids),
        }

        approval_statuses = [
            MembershipRequest.Status.approved,
            MembershipRequest.Status.rejected,
            MembershipRequest.Status.ignored,
        ]
        approval_qs = MembershipRequest.objects.filter(
            requested_at__isnull=False,
            decided_at__isnull=False,
            status__in=approval_statuses,
        )
        if trend_start is not None:
            approval_qs = approval_qs.filter(decided_at__gte=trend_start)

        approval_outlier_cutoff_days = int(settings.MEMBERSHIP_STATS_APPROVAL_OUTLIER_DAYS)
        approval_outlier_cutoff_seconds = approval_outlier_cutoff_days * 24 * 60 * 60
        approval_durations_hours: list[int] = []
        for requested_at, decided_at in approval_qs.values_list("requested_at", "decided_at"):
            duration_seconds = int((decided_at - requested_at).total_seconds())
            if duration_seconds < 0:
                continue
            if duration_seconds > approval_outlier_cutoff_seconds:
                continue
            approval_durations_hours.append(duration_seconds // 3600)

        approval_mean_hours: int | None = None
        approval_median_hours: int | None = None
        approval_p90_hours: int | None = None
        if approval_durations_hours:
            sorted_durations = sorted(approval_durations_hours)
            approval_mean_hours = int(round(float(statistics.fmean(sorted_durations))))
            approval_median_hours = int(round(float(statistics.median(sorted_durations))))
            p90_rank = max(1, math.ceil(0.9 * len(sorted_durations)))
            approval_p90_hours = int(sorted_durations[p90_rank - 1])

        summary["approval_time"] = {
            "mean_hours": approval_mean_hours,
            "median_hours": approval_median_hours,
            "p90_hours": approval_p90_hours,
            "sample_size": len(approval_durations_hours),
            "outlier_cutoff_days": approval_outlier_cutoff_days,
        }

        membership_type_rows = (
            active_memberships.values("membership_type_id", "membership_type__name")
            .annotate(count=Count("id"))
            .order_by("membership_type__name")
        )
        membership_type_labels: list[str] = [r["membership_type__name"] for r in membership_type_rows]
        membership_type_counts: list[int] = [int(r["count"]) for r in membership_type_rows]

        requests_qs = MembershipRequest.objects.all()
        if trend_start is not None:
            requests_qs = requests_qs.filter(requested_at__gte=trend_start)
        request_rows = (
            requests_qs
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
        decisions_qs = MembershipRequest.objects.filter(decided_at__isnull=False).filter(status__in=decision_statuses)
        if trend_start is not None:
            decisions_qs = decisions_qs.filter(decided_at__gte=trend_start)
        decision_rows = (
            decisions_qs
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
            Membership.objects.filter(expires_at__isnull=False, expires_at__gt=now, expires_at__lte=now + datetime.timedelta(days=365))
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

        configured_cohort_limit = int(settings.MEMBERSHIP_STATS_RETENTION_COHORTS_LIMIT)
        retention_summary, retention_chart = _compute_retention_cohort_12m(
            now=now,
            cohort_limit=max(0, min(configured_cohort_limit, 12)),
        )
        summary["retention_cohort_12m"] = retention_summary
        charts["retention_cohorts_12m"] = retention_chart

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

        active_member_usernames = set(active_individual_usernames)
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

    cache_key = f"membership_stats:data:v6:days={days_param}"
    payload = cache.get_or_set(cache_key, compute_payload, timeout=300)
    return JsonResponse(payload)
