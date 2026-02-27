import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.freeipa.exceptions import FreeIPAMisconfiguredError, FreeIPAUnavailableError
from core.freeipa.group import FreeIPAGroup, get_freeipa_group_for_elections
from core.freeipa.user import FreeIPAUser
from core.models import Election, Membership

FREEIPA_UNAVAILABLE_MESSAGE = "FreeIPA is currently unavailable. Try again later."
COMMITTEE_GROUP_MISSING_MESSAGE = (
    "Election committee group is not available in FreeIPA. Contact an administrator."
)
ELIGIBLE_GROUP_MISSING_MESSAGE = "Eligible voters group is not available in FreeIPA. Contact an administrator."

logger = logging.getLogger(__name__)

_ELIGIBILITY_FACTS_CACHE_TTL_SECONDS = 30
# Marker and facts keys are written via separate cache.set calls. Under cache
# eviction pressure, one may outlive the other, so stale facts can persist for
# at most one additional TTL window after a state transition.
_ELIGIBILITY_FACTS_CACHEABLE_STATES = {Election.Status.draft, Election.Status.open}


class ElectionEligibilityError(RuntimeError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EligibleVoter:
    username: str
    weight: int


@dataclass(frozen=True)
class CandidateValidationResult:
    eligible_candidates: set[str]
    eligible_nominators: set[str]
    disqualified_candidates: set[str]
    disqualified_nominators: set[str]
    ineligible_candidates: set[str]
    ineligible_nominators: set[str]


@dataclass(frozen=True)
class EligibilityFacts:
    weight: int
    term_start_at: datetime.datetime | None
    has_any_vote_eligible: bool
    has_active_vote_eligible_at_reference: bool


@dataclass(frozen=True)
class VoteWeightLine:
    """A single membership contributing to a voter's weight."""

    label: str
    org_name: str
    votes: int


def _election_reference_datetime(*, election: Election) -> datetime.datetime:
    reference_datetime = election.start_datetime
    if election.status == Election.Status.draft:
        reference_datetime = max(election.start_datetime, timezone.now())
    return reference_datetime


def _membership_is_active_at_reference(
    *,
    expires_at: datetime.datetime | None,
    reference_datetime: datetime.datetime,
) -> bool:
    return expires_at is None or expires_at >= reference_datetime


def _membership_is_old_enough(*, created_at: datetime.datetime | None, cutoff: datetime.datetime) -> bool:
    return created_at is not None and created_at <= cutoff


def _eligibility_facts_cache_key(*, election_id: int, election_state: str) -> str:
    return f"election_eligibility_facts:v1:election:{election_id}:state:{election_state}"


def _eligibility_facts_state_cache_key(*, election_id: int) -> str:
    return f"election_eligibility_facts:v1:election:{election_id}:state_marker"


def _compute_eligibility_facts_by_username(*, election: Election) -> dict[str, EligibilityFacts]:
    reference_datetime = _election_reference_datetime(election=election)
    cutoff = reference_datetime - datetime.timedelta(days=settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS)

    weights_by_username: dict[str, int] = {}
    term_start_by_username: dict[str, datetime.datetime] = {}
    has_any_vote_eligible: set[str] = set()
    has_active_vote_eligible_at_reference: set[str] = set()

    memberships = Membership.objects.filter(
        membership_type__category__is_individual=True,
        membership_type__enabled=True,
        membership_type__votes__gt=0,
    ).values("target_username", "created_at", "expires_at", "membership_type__votes")
    for row in memberships:
        username = str(row.get("target_username") or "").strip().lower()
        if not username:
            continue

        votes = int(row.get("membership_type__votes") or 0)
        if votes <= 0:
            continue

        has_any_vote_eligible.add(username)

        start_at = row.get("created_at")
        if isinstance(start_at, datetime.datetime):
            if username not in term_start_by_username or start_at < term_start_by_username[username]:
                term_start_by_username[username] = start_at

        expires_at = row.get("expires_at")
        is_active_at_reference = False
        if expires_at is None or isinstance(expires_at, datetime.datetime):
            is_active_at_reference = _membership_is_active_at_reference(
                expires_at=expires_at,
                reference_datetime=reference_datetime,
            )
        if is_active_at_reference:
            has_active_vote_eligible_at_reference.add(username)

        created_at = start_at if isinstance(start_at, datetime.datetime) else None
        if is_active_at_reference and _membership_is_old_enough(created_at=created_at, cutoff=cutoff):
            weights_by_username[username] = weights_by_username.get(username, 0) + votes

    org_memberships = (
        Membership.objects.select_related("target_organization", "membership_type")
        .filter(
            target_organization__isnull=False,
            membership_type__enabled=True,
            membership_type__votes__gt=0,
        )
        .only(
            "target_organization__representative",
            "membership_type__votes",
            "created_at",
            "expires_at",
        )
    )
    for membership in org_memberships:
        username = str(membership.target_organization.representative or "").strip().lower()
        if not username:
            continue

        votes = int(membership.membership_type.votes or 0)
        if votes <= 0:
            continue

        has_any_vote_eligible.add(username)

        start_at = membership.created_at
        if isinstance(start_at, datetime.datetime):
            if username not in term_start_by_username or start_at < term_start_by_username[username]:
                term_start_by_username[username] = start_at

        is_active_at_reference = _membership_is_active_at_reference(
            expires_at=membership.expires_at,
            reference_datetime=reference_datetime,
        )
        if is_active_at_reference:
            has_active_vote_eligible_at_reference.add(username)

        if is_active_at_reference and _membership_is_old_enough(created_at=start_at, cutoff=cutoff):
            weights_by_username[username] = weights_by_username.get(username, 0) + votes

    usernames = set(weights_by_username) | set(term_start_by_username) | has_any_vote_eligible | has_active_vote_eligible_at_reference
    return {
        username: EligibilityFacts(
            weight=weights_by_username.get(username, 0),
            term_start_at=term_start_by_username.get(username),
            has_any_vote_eligible=username in has_any_vote_eligible,
            has_active_vote_eligible_at_reference=username in has_active_vote_eligible_at_reference,
        )
        for username in usernames
    }


def _eligibility_facts_by_username(
    *,
    election: Election,
    require_fresh: bool = False,
) -> dict[str, EligibilityFacts]:
    election_state = str(election.status)
    state_marker_key = _eligibility_facts_state_cache_key(election_id=election.id)
    previous_state = cache.get(state_marker_key)
    if isinstance(previous_state, str) and previous_state and previous_state != election_state:
        cache.delete(
            _eligibility_facts_cache_key(
                election_id=election.id,
                election_state=previous_state,
            )
        )

    if election_state not in _ELIGIBILITY_FACTS_CACHEABLE_STATES:
        cache.delete(state_marker_key)
        return _compute_eligibility_facts_by_username(election=election)

    cache_key = _eligibility_facts_cache_key(
        election_id=election.id,
        election_state=election_state,
    )
    if require_fresh:
        facts = _compute_eligibility_facts_by_username(election=election)
        cache.set(cache_key, facts, timeout=_ELIGIBILITY_FACTS_CACHE_TTL_SECONDS)
        cache.set(state_marker_key, election_state, timeout=_ELIGIBILITY_FACTS_CACHE_TTL_SECONDS)
        return facts

    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    facts = _compute_eligibility_facts_by_username(election=election)
    cache.set(cache_key, facts, timeout=_ELIGIBILITY_FACTS_CACHE_TTL_SECONDS)
    cache.set(state_marker_key, election_state, timeout=_ELIGIBILITY_FACTS_CACHE_TTL_SECONDS)
    return facts


def _raise_unavailable(exc: Exception) -> None:
    raise ElectionEligibilityError(FREEIPA_UNAVAILABLE_MESSAGE, status_code=503) from exc


def _raise_misconfigured(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ElectionEligibilityError(message, status_code=400)
    raise ElectionEligibilityError(message, status_code=400) from exc


def _get_required_group(*, group_cn: str, require_fresh: bool, missing_message: str) -> FreeIPAGroup:
    try:
        return get_freeipa_group_for_elections(cn=group_cn, require_fresh=require_fresh)
    except FreeIPAUnavailableError as exc:
        _raise_unavailable(exc)
    except FreeIPAMisconfiguredError as exc:
        _raise_misconfigured(missing_message, exc)


def _eligible_voters_from_memberships(*, election: Election, require_fresh: bool = False) -> list[EligibleVoter]:
    facts_by_username = _eligibility_facts_by_username(
        election=election,
        require_fresh=require_fresh,
    )

    return [
        EligibleVoter(username=username, weight=weight)
        for username, weight in sorted(
            ((username, facts.weight) for username, facts in facts_by_username.items()),
            key=lambda kv: kv[0].lower(),
        )
        if weight > 0
    ]


def _freeipa_group_recursive_member_usernames(
    *,
    group_cn: str,
    require_fresh: bool,
    missing_message: str,
) -> set[str]:
    root = str(group_cn or "").strip()
    if not root:
        return set()

    seen_groups: set[str] = set()
    members: set[str] = set()
    pending: list[str] = [root]

    while pending:
        cn = str(pending.pop() or "").strip()
        if not cn:
            continue
        cn_key = cn.lower()
        if cn_key in seen_groups:
            continue
        seen_groups.add(cn_key)

        group = _get_required_group(group_cn=cn, require_fresh=require_fresh, missing_message=missing_message)
        for username_raw in (group.members or []):
            username = str(username_raw or "").strip()
            if username:
                members.add(username.lower())

        for nested_cn_raw in (group.member_groups or []):
            nested_cn = str(nested_cn_raw or "").strip()
            if nested_cn:
                pending.append(nested_cn)

    return members


def eligible_voters_from_memberships(
    *,
    election: Election,
    eligible_group_cn: str | None = None,
    require_fresh: bool = False,
) -> list[EligibleVoter]:
    eligible = _eligible_voters_from_memberships(
        election=election,
        require_fresh=require_fresh,
    )

    group_cn = str(eligible_group_cn) if eligible_group_cn is not None else str(election.eligible_group_cn or "")
    group_cn = group_cn.strip()
    if not group_cn:
        return eligible

    eligible_usernames = _freeipa_group_recursive_member_usernames(
        group_cn=group_cn,
        require_fresh=require_fresh,
        missing_message=ELIGIBLE_GROUP_MISSING_MESSAGE,
    )
    if not eligible_usernames:
        return []

    return [v for v in eligible if v.username.lower() in eligible_usernames]


def eligible_vote_weight_for_username(*, election: Election, username: str) -> int:
    """Return the voting weight for a single username using the canonical eligibility logic."""
    username = str(username or "").strip()
    if not username:
        return 0

    group_cn = str(election.eligible_group_cn or "").strip()
    if group_cn:
        eligible_usernames = _freeipa_group_recursive_member_usernames(
            group_cn=group_cn,
            require_fresh=False,
            missing_message=ELIGIBLE_GROUP_MISSING_MESSAGE,
        )
        if not eligible_usernames or username.lower() not in eligible_usernames:
            return 0

    return sum(
        line.votes
        for line in vote_weight_breakdown_for_username(
            election=election,
            username=username,
        )
    )


def vote_weight_breakdown_for_username(*, election: Election, username: str) -> list[VoteWeightLine]:
    """Return per-membership vote weight breakdown for the given username.

    Applies the same eligibility criteria as canonical vote weight computation:
    membership must be active at the election reference datetime and at least
    ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS old.

    Returns individual memberships before organization memberships and includes
    only memberships that contribute votes.
    """
    username_lower = str(username or "").strip().lower()
    if not username_lower:
        return []

    reference_datetime = _election_reference_datetime(election=election)
    cutoff = reference_datetime - datetime.timedelta(days=settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS)

    lines: list[VoteWeightLine] = []

    individual_memberships = (
        Membership.objects.select_related("membership_type")
        .filter(
            target_username__iexact=username_lower,
            membership_type__category__is_individual=True,
            membership_type__enabled=True,
            membership_type__votes__gt=0,
        )
        .order_by(
            "membership_type__category__sort_order",
            "membership_type__sort_order",
            "membership_type__code",
            "pk",
        )
        .only(
            "created_at",
            "expires_at",
            "membership_type__name",
            "membership_type__votes",
        )
    )
    for membership in individual_memberships:
        votes = int(membership.membership_type.votes or 0)
        if votes <= 0:
            continue

        is_active = _membership_is_active_at_reference(
            expires_at=membership.expires_at,
            reference_datetime=reference_datetime,
        )
        if not is_active:
            continue

        if not _membership_is_old_enough(created_at=membership.created_at, cutoff=cutoff):
            continue

        lines.append(VoteWeightLine(label=membership.membership_type.name, org_name="", votes=votes))

    org_memberships = (
        Membership.objects.select_related("target_organization", "membership_type")
        .filter(
            target_organization__isnull=False,
            target_organization__representative__iexact=username_lower,
            membership_type__enabled=True,
            membership_type__votes__gt=0,
        )
        .order_by(
            "membership_type__category__sort_order",
            "membership_type__sort_order",
            "membership_type__code",
            "pk",
        )
        .only(
            "target_organization__name",
            "created_at",
            "expires_at",
            "membership_type__name",
            "membership_type__votes",
        )
    )
    for membership in org_memberships:
        if membership.target_organization is None:
            continue

        votes = int(membership.membership_type.votes or 0)
        if votes <= 0:
            continue

        is_active = _membership_is_active_at_reference(
            expires_at=membership.expires_at,
            reference_datetime=reference_datetime,
        )
        if not is_active:
            continue

        if not _membership_is_old_enough(created_at=membership.created_at, cutoff=cutoff):
            continue

        org_name = str(membership.target_organization.name or "").strip()
        lines.append(VoteWeightLine(label=membership.membership_type.name, org_name=org_name, votes=votes))

    return lines


def election_committee_disqualification(
    *,
    candidate_usernames: Iterable[str],
    nominator_usernames: Iterable[str],
    require_fresh: bool = False,
) -> tuple[set[str], set[str]]:
    committee_group = str(settings.FREEIPA_ELECTION_COMMITTEE_GROUP or "").strip()
    if not committee_group:
        _raise_misconfigured(COMMITTEE_GROUP_MISSING_MESSAGE)

    committee_usernames = _freeipa_group_recursive_member_usernames(
        group_cn=committee_group,
        require_fresh=require_fresh,
        missing_message=COMMITTEE_GROUP_MISSING_MESSAGE,
    )
    if not committee_usernames:
        return set(), set()

    candidate_conflicts = {
        str(username or "").strip()
        for username in candidate_usernames
        if str(username or "").strip().lower() in committee_usernames
    }
    nominator_conflicts = {
        str(username or "").strip()
        for username in nominator_usernames
        if str(username or "").strip().lower() in committee_usernames
    }

    return candidate_conflicts, nominator_conflicts


def _normalized_usernames(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        username = str(value or "").strip()
        if username:
            normalized.append(username)
    return normalized


def eligible_candidate_usernames(
    *,
    election: Election,
    eligible_group_cn: str | None = None,
    require_fresh: bool = False,
) -> set[str]:
    eligible_usernames = {
        voter.username
        for voter in eligible_voters_from_memberships(
            election=election,
            eligible_group_cn=eligible_group_cn,
            require_fresh=require_fresh,
        )
    }

    disqualified_candidates, _disqualified_nominators = election_committee_disqualification(
        candidate_usernames=eligible_usernames,
        nominator_usernames=(),
        require_fresh=require_fresh,
    )
    disqualified_lower = {username.lower() for username in disqualified_candidates}
    filtered = {username for username in eligible_usernames if username.lower() not in disqualified_lower}
    logger.debug(
        "Eligible candidate usernames resolved: eligible=%s disqualified=%s filtered=%s",
        len(eligible_usernames),
        len(disqualified_candidates),
        len(filtered),
    )
    if disqualified_candidates:
        logger.debug(
            "Committee-disqualified candidates filtered from eligibility: %s",
            sorted(disqualified_candidates, key=str.lower),
        )
    return filtered


def eligible_nominator_usernames(*, election: Election, require_fresh: bool = False) -> set[str]:
    return eligible_candidate_usernames(
        election=election,
        eligible_group_cn="",
        require_fresh=require_fresh,
    )


def validate_candidates_for_election(
    *,
    election: Election,
    candidate_usernames: Iterable[str],
    nominator_usernames: Iterable[str],
    eligible_group_cn: str | None = None,
    require_fresh: bool = False,
) -> CandidateValidationResult:
    candidates = _normalized_usernames(candidate_usernames)
    nominators = _normalized_usernames(nominator_usernames)

    eligible_candidates = eligible_candidate_usernames(
        election=election,
        eligible_group_cn=eligible_group_cn,
        require_fresh=require_fresh,
    )
    eligible_nominators = eligible_nominator_usernames(
        election=election,
        require_fresh=require_fresh,
    )

    disqualified_candidates, disqualified_nominators = election_committee_disqualification(
        candidate_usernames=candidates,
        nominator_usernames=nominators,
        require_fresh=require_fresh,
    )

    ineligible_candidates = {u for u in candidates if u not in eligible_candidates}
    ineligible_nominators = {u for u in nominators if u not in eligible_nominators}

    return CandidateValidationResult(
        eligible_candidates=eligible_candidates,
        eligible_nominators=eligible_nominators,
        disqualified_candidates=disqualified_candidates,
        disqualified_nominators=disqualified_nominators,
        ineligible_candidates=ineligible_candidates,
        ineligible_nominators=ineligible_nominators,
    )


def ineligible_voters_with_reasons(*, election: Election) -> list[dict[str, object]]:
    """Compute list of ineligible voters with structured reason data.

    Returns a sorted list of dicts with keys: username, reason, term_start_date,
    election_start_date, days_at_start, days_short.

    Reason values:
    - "no_membership": user has no vote-bearing membership/sponsorship at all
    - "expired": membership/sponsorship expired before the reference datetime
    - "too_new": membership/sponsorship is too recent to meet the minimum age
    """
    eligible = _eligible_voters_from_memberships(election=election)
    eligible_usernames_lower = {v.username.lower() for v in eligible if v.username.strip()}

    reference_datetime = _election_reference_datetime(election=election)
    facts_by_username = _eligibility_facts_by_username(election=election)

    # Determine the electorate: group members if eligible_group_cn is set, else all FreeIPA users.
    group_cn = str(election.eligible_group_cn or "").strip()
    if group_cn:
        electorate = {
            u.lower()
            for u in _freeipa_group_recursive_member_usernames(
                group_cn=group_cn,
                require_fresh=False,
                missing_message=ELIGIBLE_GROUP_MISSING_MESSAGE,
            )
        }
    else:
        electorate = {
            str(u.username).strip().lower()
            for u in FreeIPAUser.all()
            if str(u.username).strip()
        }

    # Apply the group filter to the eligible set (same as eligible_voters_from_memberships).
    if group_cn:
        eligible_usernames_lower = {u for u in eligible_usernames_lower if u in electorate}

    min_age_days = int(settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS)
    election_start_day = timezone.localtime(election.start_datetime).date()
    reference_day = timezone.localtime(reference_datetime).date()

    result: list[dict[str, object]] = []
    for username in sorted(electorate):
        if username in eligible_usernames_lower:
            continue

        term_start_date: str = "Unknown"
        days_at_start: int | str = ""
        days_short: int | str = ""

        facts = facts_by_username.get(username)
        start_at = facts.term_start_at if facts is not None else None
        term_start_day: datetime.date | None = None
        if isinstance(start_at, datetime.datetime):
            term_start_day = timezone.localtime(start_at).date()
            term_start_date = term_start_day.isoformat()
            days_at_start = (election_start_day - term_start_day).days

        if facts is None or not facts.has_any_vote_eligible:
            reason = "no_membership"
        elif not facts.has_active_vote_eligible_at_reference:
            reason = "expired"
        else:
            reason = "too_new"
            if term_start_day is not None:
                days_at_reference = (reference_day - term_start_day).days
                days_short = max(0, min_age_days - days_at_reference)

        result.append(
            {
                "username": username,
                "reason": reason,
                "term_start_date": term_start_date,
                "election_start_date": election_start_day.isoformat(),
                "days_at_start": days_at_start,
                "days_short": days_short,
            }
        )

    return result
