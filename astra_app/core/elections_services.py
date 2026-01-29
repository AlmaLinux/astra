from __future__ import annotations

import datetime
import json
import secrets
from dataclasses import dataclass
from urllib.parse import quote
from zoneinfo import ZoneInfo

import post_office.mail
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core.backends import FreeIPAUser
from core.elections_eligibility import eligible_voters_from_memberships
from core.email_context import (
    election_committee_email_context,
    user_email_context,
    user_email_context_from_user,
)
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    VotingCredential,
)
from core.templated_email import queue_templated_email, render_template_string
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


def _post_office_json_context(context: dict[str, object]) -> dict[str, object]:
    """Coerce context values to JSON-safe payloads for django-post-office."""
    encoded = json.dumps(context, cls=DjangoJSONEncoder)
    decoded = json.loads(encoded)
    if isinstance(decoded, dict):
        return {str(k): v for k, v in decoded.items()}
    return {}


def _format_datetime_in_timezone(*, dt: datetime.datetime | None, tz_name: str | None = None) -> str:
    if dt is None:
        return ""

    value = dt
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=timezone.UTC)

    tz_label = ""
    if tz_name:
        try:
            tz = ZoneInfo(str(tz_name))
        except Exception:
            tz = None

        if tz is not None:
            value = value.astimezone(tz)
            tz_label = f" ({tz_name})"

    return f"{value.strftime('%Y-%m-%d %H:%M')}{tz_label}"


def _jsonify_tally_result(result: object) -> dict[str, object]:
    """Normalize tally output (Decimal, tuples) to JSON-safe types."""
    serialized = json.dumps(result, cls=DjangoJSONEncoder)
    normalized = json.loads(serialized)
    if isinstance(normalized, dict):
        return normalized
    raise ElectionError("Tally result serialization failed")


def build_public_ballots_export(*, election: Election) -> dict[str, object]:
    candidates = Candidate.objects.filter(election=election).only("id", "freeipa_username")
    candidate_name_by_id: dict[int, str] = {
        int(c.id): str(c.freeipa_username or "").strip()
        for c in candidates
        if str(c.freeipa_username or "").strip()
    }

    ballots_qs = (
        Ballot.objects.filter(election=election)
        .select_related("superseded_by")
        .only(
            "ranking",
            "weight",
            "ballot_hash",
            "is_counted",
            "chain_hash",
            "previous_chain_hash",
            "superseded_by__ballot_hash",
            "created_at",
        )
        .order_by("created_at", "id")
    )

    ballots_payload: list[dict[str, object]] = []
    for ballot in ballots_qs:
        ranking_usernames: list[str] = []
        for cid in ballot.ranking or []:
            try:
                candidate_id = int(cid)
            except (TypeError, ValueError, OverflowError):
                continue
            name = candidate_name_by_id.get(candidate_id)
            ranking_usernames.append(name if name else str(candidate_id))

        ballots_payload.append(
            {
                "ranking": ranking_usernames,
                "weight": int(ballot.weight or 0),
                "ballot_hash": str(ballot.ballot_hash or ""),
                "is_counted": bool(ballot.is_counted),
                "chain_hash": str(ballot.chain_hash or ""),
                "previous_chain_hash": str(ballot.previous_chain_hash or ""),
                "superseded_by": (
                    str(ballot.superseded_by.ballot_hash)
                    if ballot.superseded_by and ballot.superseded_by.ballot_hash
                    else None
                ),
            }
        )

    last_chain_hash = ballots_qs.values_list("chain_hash", flat=True).last()
    chain_head = str(last_chain_hash or election_genesis_chain_hash(election.id))

    return {
        "ballots": ballots_payload,
        "chain_head": chain_head,
    }


def build_public_audit_export(*, election: Election) -> dict[str, object]:
    entries = (
        AuditLogEntry.objects.filter(election=election, is_public=True)
        .only("timestamp", "event_type", "payload")
        .order_by("timestamp", "id")
    )

    audit_log: list[dict[str, object]] = []
    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, dict) else {"data": entry.payload}
        audit_log.append(
            {
                "timestamp": entry.timestamp.date().isoformat(),
                "event_type": str(entry.event_type),
                "payload": payload,
            }
        )

    algorithm = {}
    if isinstance(election.tally_result, dict):
        algo = election.tally_result.get("algorithm")
        if isinstance(algo, dict):
            algorithm = algo

    return {
        "algorithm": algorithm,
        "audit_log": audit_log,
    }


def persist_public_election_artifacts(*, election: Election) -> None:
    ballots_payload = build_public_ballots_export(election=election)
    audit_payload = build_public_audit_export(election=election)

    ballots_content = ContentFile(
        json.dumps(ballots_payload, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    )
    audit_content = ContentFile(
        json.dumps(audit_payload, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    )

    election.public_ballots_file.save("public-ballots.json", ballots_content, save=False)
    election.public_audit_file.save("public-audit.json", audit_content, save=False)
    election.artifacts_generated_at = timezone.now()
    election.save(update_fields=["public_ballots_file", "public_audit_file", "artifacts_generated_at"])



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
        **election_committee_email_context(),
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
        reply_to=[settings.ELECTION_COMMITTEE_EMAIL],
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
        rendered_subject = render_template_string(subject_template or "", context)
        rendered_html = render_template_string(html_template or "", context)
        rendered_text = render_template_string(text_template or "", context)
        post_office.mail.send(
            recipients=[email],
            sender=settings.DEFAULT_FROM_EMAIL,
            subject=rendered_subject,
            html_message=rendered_html,
            message=rendered_text,
            headers={"Reply-To": settings.ELECTION_COMMITTEE_EMAIL},
            commit=True,
        )
        return

    context = _post_office_json_context(context)
    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        context=context,
        reply_to=[settings.ELECTION_COMMITTEE_EMAIL],
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
        **election_committee_email_context(),
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
    required_participating_vote_weight_total = 0
    if quorum_percent > 0 and eligible_voter_count > 0:
        # Ceil(eligible * pct / 100) with integer arithmetic.
        required_participating_voter_count = (
            eligible_voter_count * quorum_percent + 99
        ) // 100
    if quorum_percent > 0 and eligible_vote_weight_total > 0:
        required_participating_vote_weight_total = (
            eligible_vote_weight_total * quorum_percent + 99
        ) // 100

    quorum_met = bool(
        required_participating_voter_count
        and required_participating_vote_weight_total
        and participating_voter_count >= required_participating_voter_count
        and participating_vote_weight_total >= required_participating_vote_weight_total
    )

    return {
        "quorum_percent": quorum_percent,
        "quorum_required": bool(quorum_percent > 0),
        "quorum_met": quorum_met,
        "required_participating_voter_count": required_participating_voter_count,
        "required_participating_vote_weight_total": required_participating_vote_weight_total,
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
    required_participating_vote_weight_total = int(status.get("required_participating_vote_weight_total") or 0)
    quorum_met = bool(status.get("quorum_met"))
    if required_participating_voter_count and required_participating_vote_weight_total and quorum_met:
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
