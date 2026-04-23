import datetime
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.core.cache import cache
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.defaultfilters import date as format_date
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from core.country_codes import country_code_status_from_user_data
from core.freeipa.user import FreeIPAUser
from core.membership import visible_committee_membership_requests
from core.models import Membership, MembershipLog, MembershipRequest
from core.permissions import (
    ASTRA_VIEW_MEMBERSHIP,
    MEMBERSHIP_PERMISSIONS,
    has_any_membership_permission,
    json_permission_required_any,
)
from core.templatetags.core_membership_responses import membership_response_value
from core.views_utils import _normalize_str, parse_datatables_request_base

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


def _parse_membership_audit_log_datatables_request(
    request: HttpRequest,
) -> tuple[int, int, int, str, str, int | None]:
    draw, start, length = parse_datatables_request_base(
        request,
        additional_allowed_params={"q", "username", "organization"},
        allow_cache_buster=False,
    )

    order_column = _normalize_str(request.GET.get("order[0][column]"))
    order_dir = _normalize_str(request.GET.get("order[0][dir]")).lower()
    order_name = _normalize_str(request.GET.get("order[0][name]"))
    column_data = _normalize_str(request.GET.get("columns[0][data]"))
    column_name = _normalize_str(request.GET.get("columns[0][name]"))
    column_searchable = _normalize_str(request.GET.get("columns[0][searchable]")).lower()
    column_orderable = _normalize_str(request.GET.get("columns[0][orderable]")).lower()

    if (
        order_column != "0"
        or order_dir != "desc"
        or order_name != "created_at"
        or column_data != "log_id"
        or column_name != "created_at"
        or column_searchable != "true"
        or column_orderable != "true"
    ):
        raise ValueError("Invalid query parameters.")

    q = _normalize_str(request.GET.get("q"))
    username = _normalize_str(request.GET.get("username"))
    raw_org = _normalize_str(request.GET.get("organization"))
    organization_id = int(raw_org) if raw_org.isdigit() else None
    if raw_org and organization_id is None:
        raise ValueError("Invalid query parameters.")

    return draw, start, length, q, username, organization_id


def _membership_audit_log_queryset(
    *,
    q: str,
    username: str,
    organization_id: int | None,
):
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
    return logs.order_by("-created_at")


def _serialize_membership_audit_log_row(log: MembershipLog) -> dict[str, object]:
    if log.target_username:
        target = {
            "kind": "user",
            "label": log.target_username,
            "secondary_label": "",
            "deleted": False,
            "url": reverse("user-profile", args=[log.target_username]),
        }
    elif log.target_organization is not None:
        target = {
            "kind": "organization",
            "label": log.target_organization.name,
            "secondary_label": "",
            "deleted": False,
            "url": reverse("organization-detail", args=[log.target_organization.pk]),
        }
    else:
        target = {
            "kind": "organization",
            "label": log.target_organization_name,
            "secondary_label": "",
            "deleted": True,
            "url": "",
        }

    request_payload: dict[str, object] | None = None
    if log.membership_request_id is not None and log.membership_request is not None:
        responses: list[dict[str, str]] = []
        for response_row in list(log.membership_request.responses or []):
            for question, value in response_row.items():
                responses.append(
                    {
                        "question": str(question),
                        "answer_html": str(membership_response_value(value, str(question))),
                    }
                )
        request_payload = {
            "request_id": log.membership_request_id,
            "url": reverse("membership-request-detail", args=[log.membership_request_id]),
            "responses": responses,
        }

    return {
        "log_id": log.pk,
        "created_at_display": format_date(log.created_at, "r"),
        "created_at_iso": log.created_at.isoformat(),
        "actor_username": log.actor_username,
        "target": target,
        "membership_name": log.membership_type.name,
        "action": log.action,
        "action_display": log.get_action_display(),
        "expires_display": format_date(log.expires_at, "M j, Y") if log.expires_at is not None else "",
        "request": request_payload,
    }


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    username = _normalize_str(request.GET.get("username"))
    raw_org = _normalize_str(request.GET.get("organization"))
    organization_id = int(raw_org) if raw_org.isdigit() else None

    filter_label = ""
    if username:
        filter_label = username
    elif organization_id is not None:
        filter_label = f"org:{organization_id}"

    return render(
        request,
        "core/membership_audit_log_vue.html",
        {
            "initial_q": q,
            "initial_username": username,
            "initial_organization": str(organization_id) if organization_id is not None else "",
            "filter_label": filter_label,
        },
    )


@json_permission_required_any({ASTRA_VIEW_MEMBERSHIP})
def membership_audit_log_api(request: HttpRequest) -> JsonResponse:
    try:
        draw, start, length, q, username, organization_id = _parse_membership_audit_log_datatables_request(
            request
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    logs = _membership_audit_log_queryset(
        q=q,
        username=username,
        organization_id=organization_id,
    )
    records_total = logs.count()
    sliced_logs = list(logs[start : start + length])

    payload = {
        "draw": draw,
        "recordsTotal": records_total,
        "recordsFiltered": records_total,
        "data": [_serialize_membership_audit_log_row(log) for log in sliced_logs],
    }
    return JsonResponse(payload)


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_organization(request: HttpRequest, organization_id: int) -> HttpResponse:
    query = urlencode({"organization": str(organization_id)})
    return redirect(f"{reverse('membership-audit-log')}?{query}")


@permission_required(ASTRA_VIEW_MEMBERSHIP, login_url=reverse_lazy("users"))
def membership_audit_log_user(request: HttpRequest, username: str) -> HttpResponse:
    query = urlencode({"username": _normalize_str(username)})
    return redirect(f"{reverse('membership-audit-log')}?{query}")


def _parse_membership_stats_days_param(request: HttpRequest) -> tuple[str, int | None]:
    """Validate ?days= query param. Raises ValueError on invalid input."""
    days_param = _normalize_str(request.GET.get("days")) or MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET
    if days_param not in MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS:
        raise ValueError("Invalid days parameter.")
    return days_param, MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS[days_param]


@user_passes_test(has_any_membership_permission, login_url=reverse_lazy("users"))
def membership_stats(request: HttpRequest) -> HttpResponse:
    days = _normalize_str(request.GET.get("days")) or MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET
    if days not in MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS:
        days = MEMBERSHIP_STATS_DEFAULT_DAYS_PRESET

    days_presets = [
        (key, "All time" if key == "all" else f"{key} days")
        for key in MEMBERSHIP_STATS_ALLOWED_DAYS_PRESETS
    ]

    return render(
        request,
        "core/membership_stats.html",
        {
            "current_days": days,
            "days_presets": days_presets,
            "api_summary_url": reverse("api-stats-membership-summary"),
            "api_composition_charts_url": reverse("api-stats-membership-composition-charts"),
            "api_trends_charts_url": reverse("api-stats-membership-trends-charts"),
            "api_retention_chart_url": reverse("api-stats-membership-retention-chart"),
        },
    )


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def stats_membership_summary_api(request: HttpRequest) -> HttpResponse:
    """Summary cards: counts + approval_time (days-window sensitive) + retention summary."""
    try:
        days_param, days_window = _parse_membership_stats_days_param(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    now = timezone.now()
    trend_start = now - datetime.timedelta(days=days_window) if days_window is not None else None

    def compute() -> dict[str, object]:
        all_freeipa_users = FreeIPAUser.all(respect_privacy=False)
        users_by_username = {u.username: u for u in all_freeipa_users if u.username}

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

        configured_cohort_limit = int(settings.MEMBERSHIP_STATS_RETENTION_COHORTS_LIMIT)
        retention_summary, _retention_chart = _compute_retention_cohort_12m(
            now=now,
            cohort_limit=max(0, min(configured_cohort_limit, 12)),
        )
        summary["retention_cohort_12m"] = retention_summary

        return {
            "generated_at": timezone.localtime(now).isoformat(),
            "days_param": days_param,
            "summary": summary,
        }

    cache_key = f"membership_stats:summary:v1:days={days_param}"
    payload = cache.get_or_set(cache_key, compute, timeout=300)
    return JsonResponse(payload)


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def stats_membership_composition_charts_api(request: HttpRequest) -> HttpResponse:
    """Days-independent composition charts: membership types and nationality distributions."""
    now = timezone.now()

    def compute() -> dict[str, object]:
        all_freeipa_users = FreeIPAUser.all(respect_privacy=False)
        users_by_username = {u.username: u for u in all_freeipa_users if u.username}
        active_freeipa_users = [u for u in all_freeipa_users if u.is_active]

        active_memberships = Membership.objects.active()
        membership_type_rows = (
            active_memberships.values("membership_type_id", "membership_type__name")
            .annotate(count=Count("id"))
            .order_by("membership_type__name")
        )
        membership_type_labels: list[str] = [r["membership_type__name"] for r in membership_type_rows]
        membership_type_counts: list[int] = [int(r["count"]) for r in membership_type_rows]

        active_individual_usernames = list(
            active_memberships.filter(membership_type__category__is_individual=True)
            .exclude(target_username="")
            .order_by()
            .values_list("target_username", flat=True)
            .distinct()
        )

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

        return {
            "generated_at": timezone.localtime(now).isoformat(),
            "charts": {
                "membership_types": {
                    "labels": membership_type_labels,
                    "counts": membership_type_counts,
                },
                "nationality_all_users": nationality_distribution(active_freeipa_users),
                "nationality_active_members": nationality_distribution(active_member_users),
            },
        }

    cache_key = "membership_stats:composition:v1"
    payload = cache.get_or_set(cache_key, compute, timeout=300)
    return JsonResponse(payload)


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def stats_membership_trends_charts_api(request: HttpRequest) -> HttpResponse:
    """Days-dependent trend charts: requests_trend, decisions_trend, expirations_upcoming."""
    try:
        days_param, days_window = _parse_membership_stats_days_param(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    now = timezone.now()
    trend_start = now - datetime.timedelta(days=days_window) if days_window is not None else None

    def compute() -> dict[str, object]:
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
            Membership.objects.filter(
                expires_at__isnull=False,
                expires_at__gt=now,
                expires_at__lte=now + datetime.timedelta(days=365),
            )
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

        return {
            "generated_at": timezone.localtime(now).isoformat(),
            "days_param": days_param,
            "charts": {
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
            },
        }

    cache_key = f"membership_stats:trends:v1:days={days_param}"
    payload = cache.get_or_set(cache_key, compute, timeout=300)
    return JsonResponse(payload)


@json_permission_required_any(MEMBERSHIP_PERMISSIONS)
def stats_membership_retention_chart_api(request: HttpRequest) -> HttpResponse:
    """Days-independent retention cohort chart."""
    now = timezone.now()

    def compute() -> dict[str, object]:
        configured_cohort_limit = int(settings.MEMBERSHIP_STATS_RETENTION_COHORTS_LIMIT)
        _retention_summary, retention_chart = _compute_retention_cohort_12m(
            now=now,
            cohort_limit=max(0, min(configured_cohort_limit, 12)),
        )
        return {
            "generated_at": timezone.localtime(now).isoformat(),
            "charts": {
                "retention_cohorts_12m": retention_chart,
            },
        }

    cache_key = "membership_stats:retention:v1"
    payload = cache.get_or_set(cache_key, compute, timeout=300)
    return JsonResponse(payload)



