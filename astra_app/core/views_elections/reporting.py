"""Election reporting views."""

from django.contrib.auth.decorators import permission_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET

from core.elections_services import election_quorum_status
from core.models import Election
from core.permissions import ASTRA_ADD_ELECTION


def _percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part * 100.0) / whole, 2)


@require_GET
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def elections_turnout_report(request: HttpRequest) -> HttpResponse:
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

    return render(
        request,
        "core/elections_turnout_report.html",
        {
            "report_rows": report_rows,
            "chart_data": {
                "labels": chart_labels,
                "count_turnout": chart_count_turnout,
                "weight_turnout": chart_weight_turnout,
            },
        },
    )
