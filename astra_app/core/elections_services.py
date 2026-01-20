from __future__ import annotations

import datetime
import json
import secrets
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import post_office.mail
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpRequest
from django.template import Context, Template
from django.template.exceptions import TemplateSyntaxError
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core.backends import FreeIPAGroup, FreeIPAUser
from core.email_context import user_email_context, user_email_context_from_user
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    Membership,
    OrganizationSponsorship,
    VotingCredential,
)
from core.templated_email import queue_templated_email
from core.tokens import election_chain_next_hash, election_genesis_chain_hash

ELECTION_TALLY_ALGORITHM_NAME = "Meek STV (High-Precision Variant)"
ELECTION_TALLY_ALGORITHM_VERSION = "1.0"
ELECTION_TALLY_ALGORITHM_SPEC_DOC = "agent-output/architecture/002-meek-stv-complete-architecture.md (Section 10)"


class ElectionError(Exception):
    pass


class ElectionNotOpenError(ElectionError):
    pass


class InvalidCredentialError(ElectionError):
    pass


class InvalidBallotError(ElectionError):
    pass


class ElectionNotClosedError(ElectionError):
    pass


@transaction.atomic
def extend_election_end_datetime(
    *,
    election: Election,
    new_end_datetime: datetime.datetime,
    actor: str | None = None,
) -> None:
    # IMPORTANT: ModelForms populate their instance during validation. Views that
    # validate end_datetime via a ModelForm may pass an already-mutated instance.
    # Re-load under a row lock so validation compares against the persisted end.
    locked = Election.objects.select_for_update().only("id", "status", "end_datetime").get(pk=election.pk)

    if locked.status != Election.Status.open:
        raise ElectionNotOpenError("election is not open")

    old_end = locked.end_datetime
    now = timezone.now()

    if new_end_datetime <= old_end:
        raise ElectionError("End datetime must be later than the current end.")
    if new_end_datetime <= now:
        raise ElectionError("End datetime must be in the future.")

    locked.end_datetime = new_end_datetime
    locked.save(update_fields=["end_datetime", "updated_at"])

    status = election_quorum_status(election=locked)
    payload = {
        "previous_end_datetime": old_end.isoformat(),
        "new_end_datetime": new_end_datetime.isoformat(),
        **status,
    }
    if actor:
        payload["actor"] = actor
    
    AuditLogEntry.objects.create(
        election=locked,
        event_type="election_end_extended",
        payload=payload,
        is_public=True,
    )


@dataclass(frozen=True)
class BallotReceipt:
    ballot: Ballot
    nonce: str


@dataclass(frozen=True)
class EligibleVoter:
    username: str
    weight: int


def _post_office_json_context(context: dict[str, object]) -> dict[str, object]:
    # django-post-office stores template context in a DB JSON field and runs
    # model validation before saving. Canonicalizing through DjangoJSONEncoder
    # ensures all values are JSON-safe (e.g. datetimes), avoiding runtime 500s.
    encoded = json.dumps(context, cls=DjangoJSONEncoder)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ElectionError("email context must serialize to a JSON object")
    return decoded


def _format_datetime_in_timezone(*, dt: datetime.datetime, tz_name: str | None) -> str:
    """Format an aware datetime in a user-selected IANA timezone.

    We format to a human-friendly string and include the timezone name so emails
    are unambiguous.
    """

    target_tz_name = str(tz_name or "").strip() or "UTC"
    try:
        tzinfo = ZoneInfo(target_tz_name)
    except Exception:
        target_tz_name = "UTC"
        tzinfo = ZoneInfo("UTC")

    local = timezone.localtime(dt, timezone=tzinfo)
    return f"{local.strftime('%b %d, %Y %H:%M')} ({target_tz_name})"


def _render_template_string(value: str, context: dict[str, object]) -> str:
    try:
        return Template(value or "").render(Context(context))
    except TemplateSyntaxError as exc:
        raise ElectionError(str(exc)) from exc


def _jsonify_tally_result(result: dict[str, object]) -> dict[str, object]:
    quota = result.get("quota")
    if isinstance(quota, Decimal):
        quota_json: object = str(quota)
    else:
        quota_json = quota

    return {
        "quota": quota_json,
        "elected": list(result.get("elected") or []),
        "eliminated": list(result.get("eliminated") or []),
        "forced_excluded": list(result.get("forced_excluded") or []),
        "rounds": list(result.get("rounds") or []),
    }


def build_public_ballots_export(*, election: Election) -> dict[str, object]:
    candidate_usernames_by_id = dict(
        Candidate.objects.filter(election=election).values_list(
            "id",
            "freeipa_username",
        )
    )

    ballots = list(
        Ballot.objects.filter(election=election)
        .order_by("created_at", "id")
        .values(
            "ranking",
            "weight",
            "ballot_hash",
            "is_counted",
            "chain_hash",
            "previous_chain_hash",
            "superseded_by__ballot_hash",
        )
    )
    for row in ballots:
        ranking_ids = row.get("ranking") or []
        if isinstance(ranking_ids, list):
            ranking_usernames: list[str] = []
            for cid in ranking_ids:
                try:
                    candidate_id = int(cid)
                except (TypeError, ValueError):
                    continue

                username = str(candidate_usernames_by_id.get(candidate_id) or candidate_id).strip()
                if username:
                    ranking_usernames.append(username)
            row["ranking"] = ranking_usernames

        row["superseded_by"] = row.pop("superseded_by__ballot_hash")

    genesis_hash = election_genesis_chain_hash(election.id)
    chain_head = ballots[-1]["chain_hash"] if ballots else genesis_hash

    return {
        "election_id": election.id,
        "ballots": ballots,
        "chain_head": chain_head,
        "genesis_hash": genesis_hash,
    }


def build_public_audit_export(*, election: Election) -> dict[str, object]:
    rows = list(
        AuditLogEntry.objects.filter(election=election, is_public=True)
        .order_by("timestamp", "id")
        .values(
            "timestamp",
            "event_type",
            "payload",
        )
    )
    for row in rows:
        # Privacy: export only a coarse date, not a precise timestamp.
        row["timestamp"] = row["timestamp"].date().isoformat()

    algorithm: dict[str, object] | None = None
    if isinstance(election.tally_result, dict):
        algo = election.tally_result.get("algorithm")
        if isinstance(algo, dict):
            algorithm = algo

    if algorithm is None and election.status == Election.Status.tallied:
        algorithm = {
            "name": ELECTION_TALLY_ALGORITHM_NAME,
            "version": ELECTION_TALLY_ALGORITHM_VERSION,
            "specification": {
                "doc": ELECTION_TALLY_ALGORITHM_SPEC_DOC,
                "url_path": reverse("election-algorithm"),
            },
        }

    return {
        "election_id": election.id,
        "algorithm": algorithm,
        "audit_log": rows,
    }


def persist_public_election_artifacts(*, election: Election) -> None:
    ballots_payload = build_public_ballots_export(election=election)
    audit_payload = build_public_audit_export(election=election)

    ballots_json = json.dumps(ballots_payload, sort_keys=True, cls=DjangoJSONEncoder, ensure_ascii=False).encode(
        "utf-8"
    )
    audit_json = json.dumps(audit_payload, sort_keys=True, cls=DjangoJSONEncoder, ensure_ascii=False).encode("utf-8")

    try:
        if election.public_ballots_file:
            election.public_ballots_file.delete(save=False)
        if election.public_audit_file:
            election.public_audit_file.delete(save=False)

        election.public_ballots_file.save("public_ballots.json", ContentFile(ballots_json), save=False)
        election.public_audit_file.save("public_audit_log.json", ContentFile(audit_json), save=False)

        election.artifacts_generated_at = timezone.now()
        election.save(
            update_fields=[
                "public_ballots_file",
                "public_audit_file",
                "artifacts_generated_at",
                "updated_at",
            ]
        )
    except Exception as exc:
        # Best effort: avoid leaving orphaned artifact keys referenced by the DB.
        election.public_ballots_file = ""
        election.public_audit_file = ""
        election.artifacts_generated_at = None
        election.save(
            update_fields=[
                "public_ballots_file",
                "public_audit_file",
                "artifacts_generated_at",
                "updated_at",
            ]
        )
        raise ElectionError(f"Failed to store election artifacts: {exc}") from exc


def _sanitize_ranking(*, election: Election, ranking: list[int]) -> list[int]:
    allowed = set(
        Candidate.objects.filter(election=election).values_list(
            "id",
            flat=True,
        )
    )

    if not ranking:
        raise InvalidBallotError("Invalid ballot: ranking is required")

    seen: set[int] = set()
    duplicates: set[int] = set()
    invalid: set[int] = set()

    for cid in ranking:
        if cid in seen:
            duplicates.add(cid)
        else:
            seen.add(cid)

        if cid not in allowed:
            invalid.add(cid)

    if invalid:
        raise InvalidBallotError("Invalid ballot: contains candidates not in this election")

    if duplicates:
        raise InvalidBallotError("Invalid ballot: duplicate candidates")

    return ranking


def election_vote_url(*, request: HttpRequest | None, election: Election) -> str:
    rel = reverse("election-vote", args=[election.id])
    if request is not None:
        return request.build_absolute_uri(rel)
    return settings.PUBLIC_BASE_URL.rstrip("/") + rel


def election_vote_url_with_credential_fragment(
    *,
    request: HttpRequest | None,
    election: Election,
    credential_public_id: str,
) -> str:
    # Use a URL fragment so the credential is not sent to the server in the
    # request line, access logs, or Referer headers.
    return election_vote_url(request=request, election=election) + f"#credential={quote(credential_public_id)}"


def ballot_verify_url(*, request: HttpRequest | None, ballot_hash: str) -> str:
    rel = reverse("ballot-verify") + f"?receipt={quote(ballot_hash)}"
    if request is not None:
        return request.build_absolute_uri(rel)
    return settings.PUBLIC_BASE_URL.rstrip("/") + rel


def send_vote_receipt_email(
    *,
    request: HttpRequest | None,
    election: Election,
    username: str,
    email: str,
    receipt: BallotReceipt,
    tz_name: str | None = None,
) -> None:
    context: dict[str, object] = {
        **user_email_context(username=username),
        "election_id": election.id,
        "election_name": election.name,
        "election_description": election.description,
        "election_url": election.url,
        "election_start_datetime": _format_datetime_in_timezone(dt=election.start_datetime, tz_name=tz_name),
        "election_end_datetime": _format_datetime_in_timezone(dt=election.end_datetime, tz_name=tz_name),
        "election_number_of_seats": election.number_of_seats,
        "ballot_hash": receipt.ballot.ballot_hash,
        "nonce": receipt.nonce,
        "weight": receipt.ballot.weight,
        "previous_chain_hash": receipt.ballot.previous_chain_hash,
        "chain_hash": receipt.ballot.chain_hash,
        "verify_url": ballot_verify_url(request=request, ballot_hash=receipt.ballot.ballot_hash),
    }

    context = _post_office_json_context(context)

    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME,
        context=context,
    )


def send_voting_credential_email(
    *,
    request: HttpRequest | None,
    election: Election,
    username: str,
    email: str,
    credential_public_id: str,
    tz_name: str | None = None,
    subject_template: str | None = None,
    html_template: str | None = None,
    text_template: str | None = None,
) -> None:
    context = build_voting_credential_email_context(
        request=request,
        election=election,
        username=username,
        credential_public_id=credential_public_id,
        tz_name=tz_name,
    )

    if subject_template is not None or html_template is not None or text_template is not None:
        rendered_subject = _render_template_string(subject_template or "", context)
        rendered_html = _render_template_string(html_template or "", context)
        rendered_text = _render_template_string(text_template or "", context)
        post_office.mail.send(
            recipients=[email],
            sender=settings.DEFAULT_FROM_EMAIL,
            subject=rendered_subject,
            html_message=rendered_html,
            message=rendered_text,
            commit=True,
        )
        return

    context = _post_office_json_context(context)
    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        context=context,
    )


def build_voting_credential_email_context(
    *,
    request: HttpRequest | None,
    election: Election,
    username: str,
    credential_public_id: str,
    tz_name: str | None = None,
    user: FreeIPAUser | None = None,
) -> dict[str, object]:
    """Build template context for election voting credential emails.

    This is shared by direct credential delivery and by the Send Mail tool deep-link
    used for reminder/extension announcements.
    """

    user_context = user_email_context_from_user(user=user) if user is not None else user_email_context(username=username)

    return {
        **user_context,
        "election_id": election.id,
        "election_name": election.name,
        "election_description": election.description,
        "election_url": election.url,
        "election_start_datetime": _format_datetime_in_timezone(dt=election.start_datetime, tz_name=tz_name),
        "election_end_datetime": _format_datetime_in_timezone(dt=election.end_datetime, tz_name=tz_name),
        "election_number_of_seats": election.number_of_seats,
        "credential_public_id": credential_public_id,
        "vote_url": election_vote_url(
            request=request,
            election=election,
        ),
        "vote_url_with_credential_fragment": election_vote_url_with_credential_fragment(
            request=request,
            election=election,
            credential_public_id=credential_public_id,
        ),
    }


def _eligible_voters_from_memberships(*, election: Election) -> list[EligibleVoter]:
    # Eligibility: must hold a non-expired individual membership that started at least
    # ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS (configured as 90 days) before election start.
    reference_datetime = election.start_datetime
    if election.status == Election.Status.draft:
        reference_datetime = max(election.start_datetime, timezone.now())

    cutoff = reference_datetime - datetime.timedelta(days=settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS)
    eligible_qs = (
        Membership.objects.filter(
            membership_type__isIndividual=True,
            membership_type__enabled=True,
            membership_type__votes__gt=0,
            created_at__lte=cutoff,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=reference_datetime))
        .values("target_username")
        .annotate(weight=Sum("membership_type__votes"))
        .order_by("target_username")
    )

    weights_by_username: dict[str, int] = {}
    for row in eligible_qs:
        username = str(row["target_username"])
        weight = int(row["weight"] or 0)
        if not username.strip() or weight <= 0:
            continue
        weights_by_username[username] = weights_by_username.get(username, 0) + weight

    sponsorships = (
        OrganizationSponsorship.objects.select_related("organization", "membership_type")
        .filter(
            membership_type__enabled=True,
            membership_type__votes__gt=0,
            created_at__lte=cutoff,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=reference_datetime))
        .only(
            "organization__representative",
            "membership_type__votes",
        )
    )

    for sponsorship in sponsorships:
        votes = int(sponsorship.membership_type.votes or 0)
        if votes <= 0:
            continue

        username = str(sponsorship.organization.representative or "").strip()
        if not username:
            continue
        weights_by_username[username] = weights_by_username.get(username, 0) + votes

    eligible: list[EligibleVoter] = [
        EligibleVoter(username=username, weight=weight)
        for username, weight in sorted(weights_by_username.items(), key=lambda kv: kv[0].lower())
        if weight > 0
    ]
    return eligible


def _freeipa_group_recursive_member_usernames(*, group_cn: str) -> set[str]:
    """Return lowercased usernames in the given FreeIPA group, including nested groups."""

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

        group = FreeIPAGroup.get(cn)
        if group is None:
            continue

        for username_raw in (group.members or []):
            username = str(username_raw or "").strip()
            if username:
                members.add(username.lower())

        for nested_cn_raw in (group.member_groups or []):
            nested_cn = str(nested_cn_raw or "").strip()
            if nested_cn:
                pending.append(nested_cn)

    return members


def eligible_voters_from_memberships(*, election: Election) -> list[EligibleVoter]:
    """Compute the eligible voters for an election from memberships.

    This is used both for issuing credentials and for admin visibility.
    """

    eligible = _eligible_voters_from_memberships(election=election)

    group_cn = str(election.eligible_group_cn or "").strip()
    if not group_cn:
        return eligible

    eligible_usernames = _freeipa_group_recursive_member_usernames(group_cn=group_cn)
    if not eligible_usernames:
        return []

    return [v for v in eligible if v.username.lower() in eligible_usernames]


def eligible_vote_weight_for_username(*, election: Election, username: str) -> int:
    """Return the election vote weight for a specific user, or 0 if ineligible."""

    username = str(username or "").strip()
    if not username:
        return 0

    group_cn = str(election.eligible_group_cn or "").strip()
    if group_cn:
        eligible_usernames = _freeipa_group_recursive_member_usernames(group_cn=group_cn)
        if not eligible_usernames or username.lower() not in eligible_usernames:
            return 0

    reference_datetime = election.start_datetime
    if election.status == Election.Status.draft:
        reference_datetime = max(election.start_datetime, timezone.now())

    cutoff = reference_datetime - datetime.timedelta(days=settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS)
    membership_weight = (
        Membership.objects.filter(
            target_username=username,
            membership_type__isIndividual=True,
            membership_type__enabled=True,
            membership_type__votes__gt=0,
            created_at__lte=cutoff,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=reference_datetime))
        .aggregate(weight=Sum("membership_type__votes"))
        .get("weight")
        or 0
    )

    sponsorship_weight = 0

    sponsorships = (
        OrganizationSponsorship.objects.select_related("organization", "membership_type")
        .filter(
            membership_type__enabled=True,
            membership_type__votes__gt=0,
            created_at__lte=cutoff,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=reference_datetime))
        .only(
            "organization__representative",
            "membership_type__votes",
        )
    )
    for sponsorship in sponsorships:
        votes = int(sponsorship.membership_type.votes or 0)
        if votes <= 0:
            continue

        if sponsorship.organization.representative == username:
            sponsorship_weight += votes

    return int(membership_weight) + sponsorship_weight


def election_quorum_status(*, election: Election) -> dict[str, int | bool]:
    """Return the election's current quorum/turnout status.

    Prefer issued credentials when they exist, since they represent the
    election's frozen eligibility snapshot.
    """

    quorum_percent = int(election.quorum or 0)

    credentials_qs = VotingCredential.objects.filter(election=election, weight__gt=0)
    if election.status != Election.Status.draft:
        cred_agg = credentials_qs.aggregate(voters=Count("id"), votes=Sum("weight"))
        eligible_voter_count = int(cred_agg.get("voters") or 0)
        eligible_vote_weight_total = int(cred_agg.get("votes") or 0)
    else:
        eligible = eligible_voters_from_memberships(election=election)
        eligible_voter_count = len(eligible)
        eligible_vote_weight_total = sum(v.weight for v in eligible)

    ballot_agg = Ballot.objects.filter(election=election, superseded_by__isnull=True).aggregate(
        ballots=Count("id"),
        weight_total=Sum("weight"),
    )
    participating_voter_count = int(ballot_agg.get("ballots") or 0)
    participating_vote_weight_total = int(ballot_agg.get("weight_total") or 0)

    required_participating_voter_count = 0
    if quorum_percent > 0 and eligible_voter_count > 0:
        # Ceil(eligible * pct / 100) with integer arithmetic.
        required_participating_voter_count = (
            eligible_voter_count * quorum_percent + 99
        ) // 100

    quorum_met = bool(
        required_participating_voter_count
        and participating_voter_count >= required_participating_voter_count
    )

    return {
        "quorum_percent": quorum_percent,
        "quorum_met": quorum_met,
        "required_participating_voter_count": required_participating_voter_count,
        "eligible_voter_count": eligible_voter_count,
        "eligible_vote_weight_total": eligible_vote_weight_total,
        "participating_voter_count": participating_voter_count,
        "participating_vote_weight_total": participating_vote_weight_total,
    }


@transaction.atomic
def submit_ballot(*, election: Election, credential_public_id: str, ranking: list[int]) -> BallotReceipt:
    if election.status != Election.Status.open:
        raise ElectionNotOpenError("election is not open")

    try:
        credential = VotingCredential.objects.select_for_update().get(
            election=election,
            public_id=credential_public_id,
        )
    except VotingCredential.DoesNotExist as exc:
        raise InvalidCredentialError("invalid credential") from exc

    sanitized_ranking = _sanitize_ranking(election=election, ranking=ranking)
    weight = int(credential.weight)

    # Include a random nonce in the hash input so identical re-submissions get
    # distinct receipts. This nonce is intentionally not stored.
    nonce = secrets.token_hex(16)
    ballot_hash = Ballot.compute_hash(
        election_id=election.id,
        credential_public_id=credential_public_id,
        ranking=sanitized_ranking,
        weight=weight,
        nonce=nonce,
    )

    # Commitment chaining is per-election; lock the election row so concurrent
    # submissions can't both claim the same previous chain head.
    Election.objects.select_for_update().only("id").get(pk=election.pk)

    last_ballot = (
        Ballot.objects.select_for_update()
        .filter(election=election)
        .order_by("-created_at", "-id")
        .first()
    )
    genesis_hash = election_genesis_chain_hash(election.id)
    previous_chain_hash = str(last_ballot.chain_hash if last_ballot is not None else genesis_hash)
    chain_hash = election_chain_next_hash(previous_chain_hash=previous_chain_hash, ballot_hash=ballot_hash)

    current = (
        Ballot.objects.select_for_update()
        .filter(
            election=election,
            credential_public_id=credential_public_id,
            superseded_by__isnull=True,
        )
        .order_by("-id")
        .first()
    )

    supersedes_ballot_hash = ""
    if current is None:
        ballot = Ballot.objects.create(
            election=election,
            credential_public_id=credential_public_id,
            ranking=sanitized_ranking,
            weight=weight,
            ballot_hash=ballot_hash,
            previous_chain_hash=previous_chain_hash,
            chain_hash=chain_hash,
            is_counted=True,
        )
    else:
        supersedes_ballot_hash = str(current.ballot_hash or "").strip()

        # We need to avoid violating the partial unique constraint on
        # (election, credential_public_id) where superseded_by IS NULL.
        # Create the new ballot in a temporary state, then flip the pointers.
        ballot = Ballot.objects.create(
            election=election,
            credential_public_id=credential_public_id,
            ranking=sanitized_ranking,
            weight=weight,
            ballot_hash=ballot_hash,
            previous_chain_hash=previous_chain_hash,
            chain_hash=chain_hash,
            superseded_by=current,
            is_counted=False,
        )

        Ballot.objects.filter(pk=current.pk).update(
            superseded_by=ballot,
            is_counted=False,
        )
        Ballot.objects.filter(pk=ballot.pk).update(
            superseded_by=None,
            is_counted=True,
        )
        ballot.refresh_from_db(fields=["superseded_by", "is_counted"])

    payload: dict[str, object] = {"ballot_hash": ballot_hash}
    if supersedes_ballot_hash:
        payload["supersedes_ballot_hash"] = supersedes_ballot_hash

    AuditLogEntry.objects.create(
        election=election,
        event_type="ballot_submitted",
        payload=payload,
        is_public=False,
    )

    status = election_quorum_status(election=election)
    required_participating_voter_count = int(status.get("required_participating_voter_count") or 0)
    quorum_met = bool(status.get("quorum_met"))
    if required_participating_voter_count and quorum_met:
        already_logged = AuditLogEntry.objects.filter(election=election, event_type="quorum_reached").exists()
        if not already_logged:
            AuditLogEntry.objects.create(
                election=election,
                event_type="quorum_reached",
                payload=status,
                is_public=True,
            )

    return BallotReceipt(
        ballot=ballot,
        nonce=nonce,
    )


@transaction.atomic
def issue_voting_credential(*, election: Election, freeipa_username: str, weight: int) -> VotingCredential:
    if not freeipa_username.strip():
        raise ElectionError("freeipa_username is required")
    if weight <= 0:
        raise ElectionError("weight must be positive")
    if election.status in {Election.Status.closed, Election.Status.tallied}:
        raise ElectionError("cannot issue credentials for a closed election")

    try:
        credential = VotingCredential.objects.select_for_update().get(
            election=election,
            freeipa_username=freeipa_username,
        )
    except VotingCredential.DoesNotExist:
        credential = None

    if credential is not None:
        if credential.weight != weight:
            credential.weight = weight
            credential.save(update_fields=["weight"])
        return credential

    while True:
        public_id = VotingCredential.generate_public_id()
        try:
            return VotingCredential.objects.create(
                election=election,
                public_id=public_id,
                freeipa_username=freeipa_username,
                weight=weight,
            )
        except IntegrityError:
            # Another process may have created the credential concurrently, or we hit a
            # (very unlikely) public_id collision. In either case, retry by fetching.
            try:
                credential = VotingCredential.objects.get(
                    election=election,
                    freeipa_username=freeipa_username,
                )
            except VotingCredential.DoesNotExist:
                continue

            if credential.weight != weight:
                credential.weight = weight
                credential.save(update_fields=["weight"])
            return credential


@transaction.atomic
def anonymize_election(*, election: Election) -> dict[str, int]:
    """Anonymize election credentials and scrub sensitive emails.

    Returns a dict with 'credentials_affected' and 'emails_scrubbed' counts.
    """
    if election.status not in {Election.Status.closed, Election.Status.tallied}:
        raise ElectionNotClosedError("election must be closed or tallied to anonymize")

    credentials_affected = VotingCredential.objects.filter(
        election=election, freeipa_username__isnull=False
    ).update(freeipa_username=None)

    emails_scrubbed = scrub_election_emails(election=election)

    AuditLogEntry.objects.create(
        election=election,
        event_type="election_anonymized",
        payload={
            "credentials_affected": credentials_affected,
            "emails_scrubbed": emails_scrubbed,
        },
        is_public=True,
    )

    return {"credentials_affected": credentials_affected, "emails_scrubbed": emails_scrubbed}


@transaction.atomic
def issue_voting_credentials_from_memberships(*, election: Election) -> int:
    if election.status in {Election.Status.closed, Election.Status.tallied}:
        raise ElectionError("cannot issue credentials for a closed election")
    eligible = eligible_voters_from_memberships(election=election)
    for voter in eligible:
        issue_voting_credential(election=election, freeipa_username=voter.username, weight=voter.weight)
    return len(eligible)


@transaction.atomic
def issue_voting_credentials_from_memberships_detailed(*, election: Election) -> list[VotingCredential]:
    if election.status in {Election.Status.closed, Election.Status.tallied}:
        raise ElectionError("cannot issue credentials for a closed election")

    eligible = eligible_voters_from_memberships(election=election)
    issued: list[VotingCredential] = []
    for voter in eligible:
        credential = issue_voting_credential(election=election, freeipa_username=voter.username, weight=voter.weight)
        issued.append(credential)
    return issued


@transaction.atomic
def scrub_election_emails(*, election: Election) -> int:
    """Delete sensitive emails (credentials, receipts) associated with the election."""
    # We identify emails by the election_id in their context.
    # post_office stores context as a JSON field.
    count, _ = Email.objects.filter(context__contains={"election_id": election.id}).delete()
    return count


@transaction.atomic
def close_election(*, election: Election, actor: str | None = None) -> None:
    election.refresh_from_db(fields=["status"])
    if election.status != Election.Status.open:
        raise ElectionError("election must be open to close")

    ended_at = timezone.now()

    try:
        last_chain_hash = (
            Ballot.objects.filter(election=election)
            .order_by("-created_at", "-id")
            .values_list("chain_hash", flat=True)
            .first()
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_head = str(last_chain_hash or genesis_hash)

        election.status = Election.Status.closed
        election.end_datetime = ended_at
        election.save(update_fields=["status", "end_datetime"])

        anonymize_election(election=election)

        payload = {"chain_head": chain_head}
        if actor:
            payload["actor"] = actor
        
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload=payload,
            is_public=True,
        )
    except Exception as exc:
        failure_payload = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if actor:
            failure_payload["actor"] = actor
        
        # Use autonomous transaction to persist audit log even if outer transaction rolls back
        try:
            with transaction.atomic():
                AuditLogEntry.objects.create(
                    election=election,
                    event_type="election_close_failed",
                    payload=failure_payload,
                    is_public=False,
                )
        except Exception:
            pass  # Don't let audit log failure mask original error
        
        raise ElectionError(
            f"Failed to close election: {exc}. "
            "Recovery: Verify database connectivity and election state, then retry. "
            "Contact an administrator if the issue persists."
        ) from exc


@transaction.atomic
def tally_election(*, election: Election, actor: str | None = None) -> dict[str, object]:
    from core.elections_meek import tally_meek
    from core.models import ExclusionGroup, ExclusionGroupCandidate

    election.refresh_from_db(fields=["status", "number_of_seats"])
    if election.status != Election.Status.closed:
        raise ElectionError("election must be closed to tally")

    try:
        candidates_qs = Candidate.objects.filter(election=election).only(
            "id",
            "freeipa_username",
            "tiebreak_uuid",
        )
        candidates: list[dict[str, object]] = [
            {"id": c.id, "name": c.freeipa_username, "tiebreak_uuid": c.tiebreak_uuid} for c in candidates_qs
        ]

        ballots_qs = Ballot.objects.filter(election=election, superseded_by__isnull=True).only("weight", "ranking")
        ballots: list[dict[str, object]] = [{"weight": b.weight, "ranking": list(b.ranking)} for b in ballots_qs]

        group_rows = list(
            ExclusionGroup.objects.filter(election=election).values("id", "public_id", "max_elected", "name")
        )
        group_candidate_rows = list(
            ExclusionGroupCandidate.objects.filter(exclusion_group__election=election).values(
                "exclusion_group_id",
                "candidate_id",
            )
        )
        candidate_ids_by_group_id: dict[int, list[int]] = {}
        for row in group_candidate_rows:
            gid = int(row["exclusion_group_id"])
            candidate_ids_by_group_id.setdefault(gid, []).append(int(row["candidate_id"]))

        exclusion_groups: list[dict[str, object]] = []
        for row in group_rows:
            gid = int(row["id"])
            exclusion_groups.append(
                {
                    "public_id": str(row["public_id"]),
                    "name": str(row["name"]),
                    "max_elected": int(row["max_elected"]),
                    "candidate_ids": candidate_ids_by_group_id.get(gid, []),
                }
            )

        raw_result = tally_meek(
            ballots=ballots,
            candidates=candidates,
            seats=int(election.number_of_seats),
            exclusion_groups=exclusion_groups,
        )
        result = _jsonify_tally_result(raw_result)

        result["algorithm"] = {
            "name": ELECTION_TALLY_ALGORITHM_NAME,
            "version": ELECTION_TALLY_ALGORITHM_VERSION,
            "specification": {
                "doc": ELECTION_TALLY_ALGORITHM_SPEC_DOC,
                "url_path": reverse("election-algorithm"),
            },
        }

        election.tally_result = result
        election.status = Election.Status.tallied
        election.save(update_fields=["tally_result", "status"])

        persist_public_election_artifacts(election=election)

        for idx, round_payload in enumerate(result.get("rounds") or [], start=1):
            AuditLogEntry.objects.create(
                election=election,
                event_type="tally_round",
                payload={
                    "round": idx,
                    **(round_payload if isinstance(round_payload, dict) else {"data": round_payload}),
                },
                is_public=True,
            )

        tally_completed_payload = {
            "quota": result.get("quota"),
            "elected": result.get("elected"),
            "eliminated": result.get("eliminated"),
            "forced_excluded": result.get("forced_excluded"),
            "method": "meek",
            "algorithm": result.get("algorithm"),
        }
        if actor:
            tally_completed_payload["actor"] = actor
        
        AuditLogEntry.objects.create(
            election=election,
            event_type="tally_completed",
            payload=tally_completed_payload,
            is_public=True,
        )

        return result
    except Exception as exc:
        failure_payload = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if actor:
            failure_payload["actor"] = actor
        
        # Use autonomous transaction to persist audit log even if outer transaction rolls back
        try:
            with transaction.atomic():
                AuditLogEntry.objects.create(
                    election=election,
                    event_type="tally_failed",
                    payload=failure_payload,
                    is_public=False,
                )
        except Exception:
            pass  # Don't let audit log failure mask original error
        
        raise ElectionError(
            f"Failed to tally election: {exc}. "
            "Recovery: Verify ballot data integrity and candidate configuration, then retry from the election detail page. "
            "The election remains in 'closed' state and can be tallied again. "
            "Contact an administrator if the issue persists."
        ) from exc
