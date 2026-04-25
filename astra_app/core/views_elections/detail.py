"""Election listing, detail view, and voter eligibility context."""

import datetime
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Prefetch
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import elections_eligibility
from core.api_pagination import paginate_detail_items, serialize_pagination
from core.avatar_providers import resolve_avatar_urls_for_users
from core.election_nominators import parse_nominator_identifier
from core.elections_eligibility import ElectionEligibilityError
from core.elections_services import candidate_username_by_id_map, election_quorum_status
from core.models import (
    AuditLogEntry,
    Candidate,
    Election,
    ExclusionGroup,
    Organization,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.templatetags._user_helpers import try_get_full_name
from core.views_elections._helpers import (
    _candidate_usernames,
    _elected_candidate_display,
    _get_active_election,
    _load_candidate_users,
    _tally_elected_ids,
)
from core.views_groups import _serialize_group_user_list_items
from core.views_utils import build_page_url_prefix, get_username, paginate_and_build_context


@require_GET
def election_algorithm(request):
    return render(request, "core/election_algorithm.html", {})


def _can_manage_elections(request: HttpRequest) -> bool:
    return request.user.has_perm(ASTRA_ADD_ELECTION)


def _enforce_election_detail_visibility(request: HttpRequest, election: Election, *, can_manage_elections: bool) -> None:
    is_staff = bool(request.user.is_staff)
    if election.status == Election.Status.draft and not (is_staff or can_manage_elections):
        raise Http404


def _visible_elections_queryset(*, can_manage_elections: bool):
    qs = (
        Election.objects.active()
        .only("id", "name", "description", "status", "start_datetime", "end_datetime")
        .order_by("-start_datetime", "id")
    )
    if not can_manage_elections:
        qs = qs.exclude(status=Election.Status.draft)
    return qs


def _vote_access_context(*, request: HttpRequest, election: Election) -> dict[str, object]:
    username = get_username(request)

    voter_votes: int | None = None
    credential_issued_at: datetime.datetime | None = None
    if election.status == Election.Status.open and username:
        credential = (
            VotingCredential.objects.filter(election=election, freeipa_username=username)
            .only("weight", "created_at")
            .first()
        )
        voter_votes = int(credential.weight or 0) if credential is not None else 0
        credential_issued_at = credential.created_at if credential is not None else None

    can_vote = election.status == Election.Status.open and bool(voter_votes and voter_votes > 0)
    return {
        "voter_votes": voter_votes,
        "credential_issued_at": credential_issued_at,
        "can_vote": can_vote,
    }


def _candidate_display_name(*, username: str, users_by_username: dict[str, object]) -> str:
    user = users_by_username.get(username)
    full_name = try_get_full_name(user).strip() if user is not None else ""
    if not full_name:
        full_name = username
    return f"{full_name} ({username})"


def _candidate_cards_context(election: Election) -> tuple[list[Candidate], list[dict[str, object]], dict[str, object]]:
    candidates = list(Candidate.objects.filter(election=election).order_by("freeipa_username", "id"))

    nominator_usernames: set[str] = set()
    nominator_organization_ids: set[int] = set()
    for candidate in candidates:
        parsed_nominator = parse_nominator_identifier(str(candidate.nominated_by or ""))
        if parsed_nominator.organization_id is not None:
            nominator_organization_ids.add(parsed_nominator.organization_id)
        elif parsed_nominator.username:
            nominator_usernames.add(parsed_nominator.username)

    users_by_username = _load_candidate_users(_candidate_usernames(candidates) | nominator_usernames)
    organizations_by_id = {
        organization.id: organization
        for organization in Organization.objects.filter(pk__in=nominator_organization_ids).only("id", "name")
    }

    candidate_cards: list[dict[str, object]] = []
    for candidate in candidates:
        candidate_user = users_by_username.get(candidate.freeipa_username)
        candidate_profile_url = reverse("user-profile", args=[candidate.freeipa_username])

        nominator_display_name = ""
        nominator_profile_username = ""
        parsed_nominator = parse_nominator_identifier(str(candidate.nominated_by or ""))
        if parsed_nominator.organization_id is not None:
            organization = organizations_by_id.get(parsed_nominator.organization_id)
            if organization is not None:
                nominator_display_name = organization.name
            else:
                nominator_display_name = str(candidate.nominated_by or "")
        elif parsed_nominator.username:
            nominator_user = users_by_username.get(parsed_nominator.username)
            if nominator_user is not None:
                nominator_display_name = try_get_full_name(nominator_user) or parsed_nominator.username
                nominator_profile_username = parsed_nominator.username
            else:
                nominator_display_name = parsed_nominator.username
                nominator_profile_username = parsed_nominator.username

        candidate_cards.append(
            {
                "candidate": candidate,
                "candidate_user": candidate_user,
                "candidate_full_name": try_get_full_name(candidate_user) or candidate.freeipa_username,
                "candidate_profile_url": candidate_profile_url,
                "nominator_display_name": nominator_display_name,
                "nominator_profile_username": nominator_profile_username,
                "nominator_profile_url": reverse("user-profile", args=[nominator_profile_username])
                if nominator_profile_username
                else None,
            }
        )

    return candidates, candidate_cards, users_by_username


def _serialize_election_list_item(election: Election, *, can_manage_elections: bool) -> dict[str, object]:
    return {
        "id": election.id,
        "name": election.name,
        "description": election.description,
        "status": election.status,
        "start_datetime": election.start_datetime.isoformat(),
        "end_datetime": election.end_datetime.isoformat(),
        "detail_url": reverse("election-detail", args=[election.id]),
        "edit_url": reverse("election-edit", args=[election.id]) if can_manage_elections else None,
    }


def _serialize_election_info_payload(
    election: Election,
    *,
    request: HttpRequest,
    can_vote: bool,
    credential_issued_at: datetime.datetime | None,
    summary_context: dict[str, object],
) -> dict[str, object]:
    start_datetime_local = timezone.localtime(election.start_datetime)
    end_datetime_local = timezone.localtime(election.end_datetime)

    return {
        "id": election.id,
        "name": election.name,
        "description": election.description,
        "url": election.url,
        "status": election.status,
        "start_datetime": election.start_datetime.isoformat(),
        "end_datetime": election.end_datetime.isoformat(),
        "start_datetime_display": start_datetime_local.strftime("%Y-%m-%d %H:%M %Z"),
        "end_datetime_display": end_datetime_local.strftime("%Y-%m-%d %H:%M %Z"),
        "number_of_seats": election.number_of_seats,
        "quorum": election.quorum,
        "eligible_group_cn": election.eligible_group_cn,
        "can_vote": can_vote,
        "viewer_email": str(request.user.email or "").strip() or None,
        "credential_issued_at": credential_issued_at.isoformat() if credential_issued_at is not None else None,
        "eligibility_min_membership_age_days": settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS,
        "show_turnout_chart": summary_context["show_turnout_chart"],
        "turnout_stats": summary_context["turnout_stats"],
        "turnout_chart_data": summary_context["turnout_chart_data"],
        "exclusion_group_messages": summary_context["exclusion_group_messages"],
        "election_is_finished": summary_context["election_is_finished"],
        "tally_winners": [
            {
                "username": winner["username"],
                "full_name": winner["full_name"],
            }
            for winner in summary_context["tally_winners"]
        ],
        "empty_seats": summary_context["empty_seats"],
    }


def _election_detail_summary_context(
    *,
    election: Election,
    candidates: list[Candidate],
    users_by_username: dict[str, object],
    can_manage_elections: bool,
) -> dict[str, object]:
    def _natural_join(items: list[str]) -> str:
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

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
        group_candidates = [candidate for candidate in group.candidates.all() if candidate.freeipa_username]
        names = [
            _candidate_display_name(username=candidate.freeipa_username, users_by_username=users_by_username)
            for candidate in group_candidates
        ]
        if not names:
            continue

        who = _natural_join(names)
        candidate_word = "candidate" if group.max_elected == 1 else "candidates"
        exclusion_group_messages.append(
            f"{who} belong to the {group.name} exclusion group: only {group.max_elected} {candidate_word} of the group can be elected."
        )

    elected_ids, empty_seats = _tally_elected_ids(election)
    candidate_username_by_id = candidate_username_by_id_map(candidates)
    tally_winners = _elected_candidate_display(
        elected_ids,
        candidate_username_by_id=candidate_username_by_id,
        users_by_username=users_by_username,
    )

    show_turnout_chart = bool(
        can_manage_elections
        and election.status in {Election.Status.open, Election.Status.closed, Election.Status.tallied}
    )

    turnout_stats: dict[str, object] = {}
    turnout_chart_data: dict[str, object] = {}
    show_turnout_stats = election.status in {Election.Status.open, Election.Status.closed, Election.Status.tallied} and (
        can_manage_elections or election.status == Election.Status.tallied
    )
    if show_turnout_stats:
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

    return {
        "show_turnout_chart": show_turnout_chart,
        "turnout_stats": turnout_stats,
        "turnout_chart_data": turnout_chart_data,
        "exclusion_group_messages": exclusion_group_messages,
        "election_is_finished": election.status in {Election.Status.closed, Election.Status.tallied},
        "tally_winners": tally_winners,
        "empty_seats": empty_seats,
    }


@require_GET
def elections_list(request):
    can_manage_elections = _can_manage_elections(request)
    return render(
        request,
        "core/elections_list.html",
        {
            "can_manage_elections": can_manage_elections,
        },
    )


@require_GET
def elections_api(request: HttpRequest) -> JsonResponse:
    can_manage_elections = _can_manage_elections(request)
    elections = list(_visible_elections_queryset(can_manage_elections=can_manage_elections))
    page_number = str(request.GET.get("page") or "1").strip()
    _, page_url_prefix = build_page_url_prefix(request.GET, page_param="page")
    page_ctx = paginate_and_build_context(elections, page_number, 30, page_url_prefix=page_url_prefix)

    return JsonResponse(
        {
            "can_manage_elections": can_manage_elections,
            "items": [
                _serialize_election_list_item(election, can_manage_elections=can_manage_elections)
                for election in page_ctx["page_obj"].object_list
            ],
            "pagination": serialize_pagination(page_ctx),
        }
    )


@require_GET
def election_detail(request, election_id: int):
    election = _get_active_election(election_id)

    can_manage_elections = _can_manage_elections(request)
    _enforce_election_detail_visibility(request, election, can_manage_elections=can_manage_elections)

    return render(
        request,
        "core/election_detail.html",
        {
            "election": election,
            "can_manage_elections": can_manage_elections,
            "eligible_q": str(request.GET.get("eligible_q") or ""),
            "ineligible_q": str(request.GET.get("ineligible_q") or ""),
        },
    )


@require_GET
def election_detail_info_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    can_manage_elections = _can_manage_elections(request)
    _enforce_election_detail_visibility(request, election, can_manage_elections=can_manage_elections)
    vote_access = _vote_access_context(request=request, election=election)
    candidates, _candidate_cards, users_by_username = _candidate_cards_context(election)
    summary_context = _election_detail_summary_context(
        election=election,
        candidates=candidates,
        users_by_username=users_by_username,
        can_manage_elections=can_manage_elections,
    )

    return JsonResponse(
        {
            "election": _serialize_election_info_payload(
                election,
                request=request,
                can_vote=bool(vote_access["can_vote"]),
                credential_issued_at=vote_access["credential_issued_at"],
                summary_context=summary_context,
            )
        }
    )


@require_GET
def election_detail_candidates_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    can_manage_elections = _can_manage_elections(request)
    _enforce_election_detail_visibility(request, election, can_manage_elections=can_manage_elections)
    _candidates, candidate_cards, users_by_username = _candidate_cards_context(election)
    page_items, page_ctx = paginate_detail_items(request, candidate_cards)
    typed_page_items = cast(list[dict[str, Any]], page_items)
    avatar_url_by_username, _avatar_resolution_count, _avatar_fallback_count = resolve_avatar_urls_for_users(
        [
            users_by_username[str(item["candidate"].freeipa_username)]
            for item in typed_page_items
            if str(item["candidate"].freeipa_username) in users_by_username
        ],
        width=120,
        height=120,
    )

    return JsonResponse(
        {
            "candidates": {
                "items": [
                    {
                        "id": item["candidate"].id,
                        "username": item["candidate"].freeipa_username,
                        "has_user": item["candidate_user"] is not None,
                        "full_name": item["candidate_full_name"],
                        "profile_url": item["candidate_profile_url"],
                        "avatar_url": avatar_url_by_username.get(item["candidate"].freeipa_username, ""),
                        "description": item["candidate"].description,
                        "url": item["candidate"].url,
                        "nominated_by": item["candidate"].nominated_by,
                        "nominator_display_name": item["nominator_display_name"],
                        "nominator_profile_username": item["nominator_profile_username"] or None,
                        "nominator_profile_url": item["nominator_profile_url"],
                    }
                    for item in typed_page_items
                ],
                "pagination": serialize_pagination(page_ctx),
            }
        }
    )


def _eligible_voters_context(*, request, election: Election, enabled: bool) -> dict[str, object]:
    """Build template context for eligible/ineligible voter grids."""
    if not enabled:
        return {}

    eligible_q = str(request.GET.get("eligible_q") or "").strip()
    eligible_page_number = str(request.GET.get("eligible_page") or "1").strip()
    ineligible_q = str(request.GET.get("ineligible_q") or "").strip()
    ineligible_page_number = str(request.GET.get("ineligible_page") or "1").strip()

    try:
        return {
            **_eligible_voters_context_data(
                request=request,
                election=election,
                q=eligible_q,
                page_number=eligible_page_number,
            ),
            **_ineligible_voters_context_data(
                request=request,
                election=election,
                q=ineligible_q,
                page_number=ineligible_page_number,
            ),
        }
    except ElectionEligibilityError as exc:
        messages.error(request, str(exc))
        return {}


def _eligible_voters_context_data(
    *,
    request: HttpRequest,
    election: Election,
    q: str,
    page_number: str,
) -> dict[str, object]:
    """Build the eligible voter grid state shared by HTML and JSON views."""
    q_lower = q.lower()

    eligible = elections_eligibility.eligible_voters_from_memberships(election=election)
    eligible_for_grid = (
        [v for v in eligible if q_lower in str(v.username or "").lower()]
        if q_lower
        else list(eligible)
    )

    usernames = [v.username for v in eligible_for_grid]
    grid_items = [{"kind": "user", "username": username} for username in usernames]

    _, page_url_prefix = build_page_url_prefix(request.GET, page_param="eligible_page")
    page_ctx = paginate_and_build_context(grid_items, page_number, 24, page_url_prefix=page_url_prefix)

    return {
        "eligible_voters": eligible,
        "eligible_voter_usernames": usernames,
        "eligible_q": q,
        "grid_items": list(page_ctx["page_obj"]),
        **page_ctx,
        "empty_label": "No eligible voters.",
    }


def _ineligible_voters_context_data(
    *,
    request: HttpRequest,
    election: Election,
    q: str,
    page_number: str,
) -> dict[str, object]:
    """Build the ineligible voter grid state shared by HTML and JSON views."""
    ineligible_voters = elections_eligibility.ineligible_voters_with_reasons(election=election)
    if q:
        q_lower = q.lower()
        ineligible_voters = [
            v
            for v in ineligible_voters
            if q_lower in str(v.get("username") or "").lower()
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

    ineligible_grid_items = [
        {"kind": "user", "username": str(v["username"])} for v in ineligible_voters if str(v.get("username") or "").strip()
    ]
    _, ineligible_page_url_prefix = build_page_url_prefix(request.GET, page_param="ineligible_page")
    ineligible_page_ctx = paginate_and_build_context(
        ineligible_grid_items, page_number, 24, page_url_prefix=ineligible_page_url_prefix,
    )

    return {
        "ineligible_voters": ineligible_voters,
        "ineligible_voter_details_by_username": ineligible_voter_details_by_username,
        "ineligible_q": q,
        "ineligible_grid_items": list(ineligible_page_ctx["page_obj"]),
        "ineligible_page_context": ineligible_page_ctx,
        **{f"ineligible_{k}": v for k, v in ineligible_page_ctx.items()},
        "ineligible_empty_label": "No ineligible voters found.",
    }


@require_GET
def election_detail_eligible_voters_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    can_manage_elections = _can_manage_elections(request)
    _enforce_election_detail_visibility(request, election, can_manage_elections=can_manage_elections)
    if not can_manage_elections:
        return JsonResponse({"error": "Permission denied."}, status=403)

    eligible_q = str(request.GET.get("q") or "").strip()
    page_number = str(request.GET.get("page") or "1").strip()
    try:
        context = _eligible_voters_context_data(
            request=request,
            election=election,
            q=eligible_q,
            page_number=page_number,
        )
    except ElectionEligibilityError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)

    page_obj = context["page_obj"]
    items = _serialize_group_user_list_items([
        str(item["username"]) for item in page_obj.object_list if str(item.get("username") or "").strip()
    ])

    return JsonResponse(
        {
            "eligible_voters": {
                "items": items,
                "usernames": context["eligible_voter_usernames"],
                "pagination": serialize_pagination(context),
            },
        }
    )


@require_GET
def election_detail_ineligible_voters_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    can_manage_elections = _can_manage_elections(request)
    _enforce_election_detail_visibility(request, election, can_manage_elections=can_manage_elections)
    if not can_manage_elections:
        return JsonResponse({"error": "Permission denied."}, status=403)

    q = str(request.GET.get("q") or "").strip()
    page_number = str(request.GET.get("page") or "1").strip()
    try:
        context = _ineligible_voters_context_data(
            request=request,
            election=election,
            q=q,
            page_number=page_number,
        )
    except ElectionEligibilityError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)

    ineligible_page_context = context["ineligible_page_context"]
    ineligible_page_obj = ineligible_page_context["page_obj"]
    items = _serialize_group_user_list_items([
        str(item["username"]) for item in ineligible_page_obj.object_list if str(item.get("username") or "").strip()
    ])

    return JsonResponse(
        {
            "ineligible_voters": {
                "items": items,
                "details_by_username": context["ineligible_voter_details_by_username"],
                "pagination": serialize_pagination(ineligible_page_context),
            },
        }
    )
