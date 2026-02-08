"""Election listing, detail view, and voter eligibility context."""

import datetime
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Prefetch
from django.db.models.functions import TruncDate
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import elections_eligibility
from core.elections_eligibility import ElectionEligibilityError
from core.elections_services import election_quorum_status
from core.models import (
    AuditLogEntry,
    Candidate,
    Election,
    ExclusionGroup,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.views_elections._helpers import (
    _elected_candidate_display,
    _get_active_election,
    _load_candidate_users,
    _tally_elected_ids,
)
from core.views_utils import get_username, paginate_and_build_context


@require_GET
def election_algorithm(request):
    return render(request, "core/election_algorithm.html", {})


@require_GET
def elections_list(request):
    can_manage_elections = request.user.has_perm(ASTRA_ADD_ELECTION)
    qs = (
        Election.objects.active()
        .only("id", "name", "status", "start_datetime", "end_datetime")
        .order_by("-start_datetime", "id")
    )
    if not can_manage_elections:
        qs = qs.exclude(status=Election.Status.draft)
    elections = list(qs)

    open_statuses = {Election.Status.open, Election.Status.draft}
    past_statuses = {Election.Status.closed, Election.Status.tallied}

    open_elections: list[Election] = []
    past_elections: list[Election] = []
    for election in elections:
        if election.status in past_statuses:
            past_elections.append(election)
        elif election.status in open_statuses:
            open_elections.append(election)
        else:
            open_elections.append(election)
    return render(
        request,
        "core/elections_list.html",
        {
            "open_elections": open_elections,
            "past_elections": past_elections,
            "can_manage_elections": can_manage_elections,
        },
    )


@require_GET
def election_detail(request, election_id: int):
    election = _get_active_election(election_id)

    can_manage_elections = request.user.has_perm(ASTRA_ADD_ELECTION)

    is_staff = bool(request.user.is_staff)
    if election.status == Election.Status.draft and not (is_staff or can_manage_elections):
        raise Http404

    candidates = list(Candidate.objects.filter(election=election).order_by("freeipa_username", "id"))

    usernames: set[str] = set()
    for c in candidates:
        if c.freeipa_username:
            usernames.add(c.freeipa_username)
        if c.nominated_by:
            usernames.add(c.nominated_by)

    users_by_username = _load_candidate_users(usernames)

    candidate_cards: list[dict[str, object]] = []
    for c in candidates:
        candidate_user = users_by_username.get(c.freeipa_username)
        nominator_user = users_by_username.get(c.nominated_by) if c.nominated_by else None
        candidate_cards.append(
            {
                "candidate": c,
                "candidate_user": candidate_user,
                "nominator_user": nominator_user,
            }
        )

    def _natural_join(items: list[str]) -> str:
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    def _candidate_display_name(username: str) -> str:
        user = users_by_username.get(username)
        full_name = user.full_name if user is not None else ""
        full_name = str(full_name or "").strip()
        if not full_name:
            full_name = username
        return f"{full_name} ({username})"

    exclusion_group_messages: list[str] = []
    exclusion_groups = list(
        ExclusionGroup.objects.filter(election=election)
        .prefetch_related(
            Prefetch(
                "candidates",
                queryset=Candidate.objects.only("id", "freeipa_username").order_by("freeipa_username", "id"),
            )
        )
        .order_by("name", "id")
    )
    for group in exclusion_groups:
        group_candidates = [c for c in group.candidates.all() if c.freeipa_username]
        names = [_candidate_display_name(c.freeipa_username) for c in group_candidates]
        if not names:
            continue

        who = _natural_join(names)
        candidate_word = "candidate" if group.max_elected == 1 else "candidates"
        exclusion_group_messages.append(
            f"{who} belong to the {group.name} exclusion group: only {group.max_elected} {candidate_word} of the group can be elected."
        )

    elected_ids, empty_seats = _tally_elected_ids(election)
    candidate_username_by_id = {c.id: c.freeipa_username for c in candidates}
    tally_winners = _elected_candidate_display(
        elected_ids,
        candidate_username_by_id=candidate_username_by_id,
        users_by_username=users_by_username,
    )

    admin_context = _eligible_voters_context(request=request, election=election, enabled=can_manage_elections)

    username = get_username(request)

    voter_votes: int | None = None
    if election.status == Election.Status.open and username:
        credential = (
            VotingCredential.objects.filter(election=election, freeipa_username=username)
            .only("weight")
            .first()
        )
        voter_votes = int(credential.weight or 0) if credential is not None else 0

    can_vote = election.status == Election.Status.open and bool(voter_votes and voter_votes > 0)

    show_turnout_chart = bool(
        can_manage_elections
        and election.status in {Election.Status.open, Election.Status.closed, Election.Status.tallied}
    )

    turnout_stats: dict[str, object] = {}
    turnout_chart_data: dict[str, object] = {}
    if can_manage_elections or election.status == Election.Status.tallied:
        status = election_quorum_status(election=election)
        eligible_voter_count = int(status.get("eligible_voter_count") or 0)
        eligible_vote_weight_total = int(status.get("eligible_vote_weight_total") or 0)
        required_participating_voter_count = int(status.get("required_participating_voter_count") or 0)
        required_participating_vote_weight_total = int(
            status.get("required_participating_vote_weight_total") or 0
        )
        participating_voter_count = int(status.get("participating_voter_count") or 0)
        participating_vote_weight_total = int(status.get("participating_vote_weight_total") or 0)
        quorum_met = bool(status.get("quorum_met"))
        quorum_required = bool(status.get("quorum_required"))
        quorum_percent = int(status.get("quorum_percent") or 0)

        participating_voter_percent = 0
        if eligible_voter_count > 0:
            participating_voter_percent = min(
                100,
                int((participating_voter_count * 100) / eligible_voter_count),
            )

        participating_vote_weight_percent = 0
        if eligible_vote_weight_total > 0:
            participating_vote_weight_percent = min(
                100,
                int((participating_vote_weight_total * 100) / eligible_vote_weight_total),
            )

        turnout_stats = {
            "participating_voter_count": participating_voter_count,
            "participating_vote_weight_total": participating_vote_weight_total,
            "eligible_voter_count": eligible_voter_count,
            "eligible_vote_weight_total": eligible_vote_weight_total,
            "required_participating_voter_count": required_participating_voter_count,
            "required_participating_vote_weight_total": required_participating_vote_weight_total,
            "quorum_met": quorum_met,
            "quorum_percent": quorum_percent,
            "quorum_required": quorum_required,
            "participating_voter_percent": participating_voter_percent,
            "participating_vote_weight_percent": participating_vote_weight_percent,
        }

        if show_turnout_chart:
            rows = (
                AuditLogEntry.objects.filter(election=election, event_type="ballot_submitted")
                .annotate(day=TruncDate("timestamp"))
                .values("day")
                .annotate(count=Count("id"))
                .order_by("day")
            )

            counts_by_day: dict[datetime.date, int] = {}
            for row in rows:
                day = row.get("day")
                if not isinstance(day, datetime.date):
                    continue
                counts_by_day[day] = int(row.get("count") or 0)

            start_day = timezone.localdate(election.start_datetime)
            if election.status == Election.Status.open:
                end_day = timezone.localdate()
            else:
                end_day = timezone.localdate(election.end_datetime)
            if end_day < start_day:
                end_day = start_day

            labels: list[str] = []
            counts: list[int] = []
            cursor = start_day
            while cursor <= end_day:
                labels.append(cursor.isoformat())
                counts.append(counts_by_day.get(cursor, 0))
                cursor += datetime.timedelta(days=1)

            turnout_chart_data = {
                "labels": labels,
                "counts": counts,
            }

    return render(
        request,
        "core/election_detail.html",
        {
            "election": election,
            "candidates": candidates,
            "candidate_cards": candidate_cards,
            "can_manage_elections": can_manage_elections,
            "can_vote": can_vote,
            "show_turnout_chart": show_turnout_chart,
            "eligibility_min_membership_age_days": settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS,
            **admin_context,
            "turnout_stats": turnout_stats,
            "turnout_chart_data": turnout_chart_data,
            "exclusion_group_messages": exclusion_group_messages,
            "election_is_finished": election.status in {Election.Status.closed, Election.Status.tallied},
            "tally_winners": tally_winners,
            "empty_seats": empty_seats,
        },
    )


def _eligible_voters_context(*, request, election: Election, enabled: bool) -> dict[str, object]:
    """Build template context for eligible/ineligible voter grids."""
    if not enabled:
        return {}

    try:
        eligible = elections_eligibility.eligible_voters_from_memberships(election=election)
    except ElectionEligibilityError as exc:
        messages.error(request, str(exc))
        return {}

    eligible_q = str(request.GET.get("eligible_q") or "").strip()
    eligible_q_lower = eligible_q.lower()
    eligible_for_grid = (
        [v for v in eligible if eligible_q_lower in str(v.username or "").lower()]
        if eligible_q_lower
        else list(eligible)
    )

    try:
        ineligible_voters = elections_eligibility.ineligible_voters_with_reasons(election=election)
    except ElectionEligibilityError as exc:
        messages.error(request, str(exc))
        return {}

    ineligible_q = str(request.GET.get("ineligible_q") or "").strip()
    if ineligible_q:
        ineligible_q_lower = ineligible_q.lower()
        ineligible_voters = [
            v
            for v in ineligible_voters
            if ineligible_q_lower in str(v.get("username") or "").lower()
        ]

    ineligible_voter_details_by_username = {
        str(v["username"]): {
            "reason": str(v["reason"]),
            "term_start_date": str(v["term_start_date"]),
            "election_start_date": str(v["election_start_date"]),
            "days_at_start": v["days_at_start"],
            "days_short": v["days_short"],
        }
        for v in ineligible_voters
        if str(v.get("username") or "").strip()
    }

    usernames = [v.username for v in eligible_for_grid]

    grid_usernames = [v.username for v in eligible_for_grid]
    grid_items = [{"kind": "user", "username": username} for username in grid_usernames]

    page_number = str(request.GET.get("eligible_page") or "1").strip()
    qs = dict(request.GET.items())
    qs.pop("eligible_page", None)
    page_url_prefix = f"?{urlencode(qs)}&eligible_page=" if qs else "?eligible_page="
    page_ctx = paginate_and_build_context(grid_items, page_number, 24, page_url_prefix=page_url_prefix)

    ineligible_grid_items = [
        {"kind": "user", "username": str(v["username"])} for v in ineligible_voters if str(v.get("username") or "").strip()
    ]
    ineligible_page_number = str(request.GET.get("ineligible_page") or "1").strip()
    ineligible_qs = dict(request.GET.items())
    ineligible_qs.pop("ineligible_page", None)
    ineligible_page_url_prefix = (
        f"?{urlencode(ineligible_qs)}&ineligible_page=" if ineligible_qs else "?ineligible_page="
    )
    ineligible_page_ctx = paginate_and_build_context(
        ineligible_grid_items, ineligible_page_number, 24, page_url_prefix=ineligible_page_url_prefix,
    )

    return {
        "eligible_voters": eligible,
        "eligible_voter_usernames": usernames,
        "eligible_q": eligible_q,
        "ineligible_voters": ineligible_voters,
        "ineligible_voter_details_by_username": ineligible_voter_details_by_username,
        "ineligible_q": ineligible_q,
        "ineligible_grid_items": list(ineligible_page_ctx["page_obj"]),
        **{f"ineligible_{k}": v for k, v in ineligible_page_ctx.items()},
        "ineligible_empty_label": "No ineligible voters found.",
        "grid_items": list(page_ctx["page_obj"]),
        **page_ctx,
        "empty_label": "No eligible voters.",
    }
