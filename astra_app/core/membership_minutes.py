"""Aggregate audit-log data into structured input for the committee minutes.

This module contains only data aggregation: it produces a JSON-serialisable
payload describing what happened to membership requests during a date range.
The human-readable minutes wording/formatting lives entirely in the frontend
(Vue) layer so it can be tweaked without touching Python.
"""

from __future__ import annotations

import datetime
from collections import defaultdict

from django.conf import settings
from django.db.models import Q
from django.urls import reverse

from core.membership_constants import MembershipCategoryCode
from core.models import MembershipLog, MembershipRequest

# Actions that represent a request-level decision/state transition. Other
# actions (representative_changed, expiry_changed, terminated) are membership
# lifecycle events that do not change a request's effective status.
_STATUS_BY_ACTION: dict[str, str] = {
    MembershipLog.Action.requested: MembershipRequest.Status.pending,
    MembershipLog.Action.resubmitted: MembershipRequest.Status.pending,
    MembershipLog.Action.reopened: MembershipRequest.Status.pending,
    MembershipLog.Action.on_hold: MembershipRequest.Status.on_hold,
    MembershipLog.Action.approved: MembershipRequest.Status.approved,
    MembershipLog.Action.rejected: MembershipRequest.Status.rejected,
    MembershipLog.Action.ignored: MembershipRequest.Status.ignored,
    MembershipLog.Action.rescinded: MembershipRequest.Status.rescinded,
}

# A request counts as "received or updated during the period" when it was newly
# requested or resubmitted (an applicant-side update) within the window.
_INTAKE_ACTIONS: frozenset[str] = frozenset(
    {MembershipLog.Action.requested, MembershipLog.Action.resubmitted}
)

# Order in which category sections appear in the minutes.
_CATEGORY_ORDER: tuple[str, ...] = (
    MembershipCategoryCode.individual,
    MembershipCategoryCode.mirror,
    MembershipCategoryCode.sponsorship,
)

# Statuses that take a request out of scope entirely (never reported).
_EXCLUDED_STATUSES: frozenset[str] = frozenset(
    {MembershipRequest.Status.ignored, MembershipRequest.Status.rescinded}
)


def _window_bounds(
    start_date: datetime.date, end_date: datetime.date
) -> tuple[datetime.datetime, datetime.datetime]:
    """Return the inclusive [start 00:00:00, end 23:59:59.999999] UTC window."""
    window_start = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.UTC)
    window_end = datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.UTC)
    return window_start, window_end


def _request_url(request_id: int) -> str:
    path = reverse("membership-request-detail", args=[request_id])
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"


def _effective_status(
    logs: list[MembershipLog],
    *,
    as_of: datetime.datetime,
    inclusive: bool,
) -> tuple[str | None, str]:
    """Replay ordered logs and return the request's decision status at ``as_of``.

    ``logs`` must be ordered by ``created_at`` ascending. Returns the latest
    decision status reached by ``as_of`` (``None`` if the request did not exist
    yet) along with the reason attached to the log that produced it.
    """
    status: str | None = None
    reason = ""
    for log in logs:
        if inclusive:
            if log.created_at > as_of:
                break
        elif log.created_at >= as_of:
            break
        mapped = _STATUS_BY_ACTION.get(log.action)
        if mapped is None:
            continue
        status = mapped
        reason = log.rejection_reason or ""
    return status, reason


def _candidate_request_ids(
    window_start: datetime.datetime, window_end: datetime.datetime
) -> set[int]:
    """Superset of requests that might be in scope; classification filters it."""
    activity_ids = set(
        MembershipLog.objects.filter(
            created_at__gte=window_start, created_at__lte=window_end
        ).values_list("membership_request_id", flat=True)
    )
    activity_ids.discard(None)

    # Requests that could still have been open at the start of the window: those
    # created by the window end and not yet decided before it began.
    open_ids = set(
        MembershipRequest.objects.filter(requested_at__lte=window_end)
        .filter(Q(decided_at__isnull=True) | Q(decided_at__gte=window_start))
        .values_list("id", flat=True)
    )
    return activity_ids | open_ids


def _empty_category(code: str) -> dict[str, object]:
    return {"code": code, "rfi": [], "declined": [], "accepted": []}


def build_minutes_data(start_date: datetime.date, end_date: datetime.date) -> dict[str, object]:
    """Build the structured minutes payload for the inclusive date range."""
    window_start, window_end = _window_bounds(start_date, end_date)

    candidate_ids = _candidate_request_ids(window_start, window_end)

    categories: dict[str, dict[str, object]] = {code: _empty_category(code) for code in _CATEGORY_ORDER}
    summary = {
        "received_or_updated": 0,
        "earlier_pending": 0,
        "rfi": 0,
        "declined": 0,
        "accepted": 0,
        "pending": 0,
    }

    if candidate_ids:
        requests = (
            MembershipRequest.objects.filter(id__in=candidate_ids)
            .select_related("membership_type", "membership_type__category")
        )

        logs_by_request: dict[int, list[MembershipLog]] = defaultdict(list)
        for log in MembershipLog.objects.filter(
            membership_request_id__in=candidate_ids, created_at__lte=window_end
        ).order_by("created_at", "id"):
            logs_by_request[log.membership_request_id].append(log)

        for req in requests:
            request_logs = logs_by_request.get(req.id, [])

            had_intake = any(
                log.action in _INTAKE_ACTIONS and window_start <= log.created_at <= window_end
                for log in request_logs
            )
            status_before, _ = _effective_status(request_logs, as_of=window_start, inclusive=False)

            if had_intake:
                group = "received_or_updated"
            elif status_before in {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold}:
                group = "earlier_pending"
            else:
                # Decided before the window with no intake in it (e.g. a stray
                # expiry/representative log): not part of these minutes.
                continue

            status_end, reason_end = _effective_status(request_logs, as_of=window_end, inclusive=True)
            if status_end is None or status_end in _EXCLUDED_STATUSES:
                continue

            summary[group] += 1

            category_code = req.membership_type.category_id
            category = categories.get(category_code)
            record: dict[str, object] = {
                "id": req.id,
                "url": _request_url(req.id),
                "membership_type_name": req.membership_type.name,
            }

            if status_end == MembershipRequest.Status.on_hold:
                summary["rfi"] += 1
                record["reason"] = reason_end
                if category is not None:
                    category["rfi"].append(record)  # type: ignore[union-attr]
            elif status_end == MembershipRequest.Status.rejected:
                summary["declined"] += 1
                record["reason"] = reason_end
                if category is not None:
                    category["declined"].append(record)  # type: ignore[union-attr]
            elif status_end == MembershipRequest.Status.approved:
                summary["accepted"] += 1
                if category is not None:
                    category["accepted"].append(record)  # type: ignore[union-attr]
            else:  # pending
                summary["pending"] += 1

        for category in categories.values():
            for bucket in ("rfi", "declined", "accepted"):
                category[bucket].sort(key=lambda record: record["id"])  # type: ignore[union-attr,index]

    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "summary": summary,
        "categories": [categories[code] for code in _CATEGORY_ORDER],
    }
