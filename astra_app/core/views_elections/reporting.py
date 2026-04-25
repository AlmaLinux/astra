"""Election reporting views."""

from django.contrib.auth.decorators import permission_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET

from core.elections_services import election_quorum_status
from core.models import Election
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required


def _percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part * 100.0) / whole, 2)


def _build_elections_turnout_report() -> tuple[list[dict[str, object]], dict[str, list[float] | list[str]]]:
    elections = list(
        Election.objects.active()
        .filter(status__in=[Election.Status.open, Election.Status.closed, Election.Status.tallied])
        .exclude(status=Election.Status.draft)
        .annotate(candidates_count=Count("candidates", distinct=True), credentials_count=Count("credentials", distinct=True))
        .order_by("start_datetime", "id")
    )

    report_rows: list[dict[str, object]] = []
    chart_labels: list[str] = []
    chart_count_turnout: list[float] = []
    chart_weight_turnout: list[float] = []

    for election in elections:
        status = election_quorum_status(election=election)
        eligible_count = int(status.get("eligible_voter_count") or 0)
        eligible_weight = int(status.get("eligible_vote_weight_total") or 0)
        participating_count = int(status.get("participating_voter_count") or 0)
        participating_weight = int(status.get("participating_vote_weight_total") or 0)

        credentials_issued = int(election.credentials_count or 0) > 0
        turnout_count_pct = _percent(participating_count, eligible_count)
        turnout_weight_pct = _percent(participating_weight, eligible_weight)

        candidates_count = int(election.candidates_count or 0)
        seats = int(election.number_of_seats or 0)
        contest_ratio = round(candidates_count / seats, 2) if seats > 0 else 0.0

        report_rows.append(
            {
                "election": election,
                "eligible_count": eligible_count,
                "eligible_weight": eligible_weight,
                "participating_count": participating_count,
                "participating_weight": participating_weight,
                "turnout_count_pct": turnout_count_pct,
                "turnout_weight_pct": turnout_weight_pct,
                "candidates_count": candidates_count,
                "seats": seats,
                "contest_ratio": contest_ratio,
                "credentials_issued": credentials_issued,
            }
        )

        start_date = election.start_datetime.date().isoformat() if election.start_datetime else "unknown"
        chart_labels.append(f"{start_date}: {election.name or f'Election {election.id}'}")
        chart_count_turnout.append(turnout_count_pct)
        chart_weight_turnout.append(turnout_weight_pct)

    return (
        report_rows,
        {
            "labels": chart_labels,
            "count_turnout": chart_count_turnout,
            "weight_turnout": chart_weight_turnout,
        },
    )


def _serialize_turnout_report_row(row: dict[str, object]) -> dict[str, object]:
    election = row["election"]
    if not isinstance(election, Election):
        raise TypeError("row['election'] must be an Election")

    return {
        "election": {
            "id": election.id,
            "name": election.name,
            "status": election.status,
            "start_date": election.start_datetime.date().isoformat() if election.start_datetime else "",
            "detail_url": reverse_lazy("election-detail", args=[election.id]),
        },
        "eligible_count": row["eligible_count"],
        "eligible_weight": row["eligible_weight"],
        "participating_count": row["participating_count"],
        "participating_weight": row["participating_weight"],
        "turnout_count_pct": row["turnout_count_pct"],
        "turnout_weight_pct": row["turnout_weight_pct"],
        "candidates_count": row["candidates_count"],
        "seats": row["seats"],
        "contest_ratio": row["contest_ratio"],
        "credentials_issued": row["credentials_issued"],
    }


@require_GET
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def elections_turnout_report(request: HttpRequest) -> HttpResponse:
    return render(request, "core/elections_turnout_report.html")


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def elections_turnout_report_api(request: HttpRequest) -> JsonResponse:
    report_rows, chart_data = _build_elections_turnout_report()
    return JsonResponse(
        {
            "rows": [_serialize_turnout_report_row(row) for row in report_rows],
            "chart_data": chart_data,
        }
    )
