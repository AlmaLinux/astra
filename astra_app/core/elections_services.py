import datetime
import json
import logging
import secrets
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import batched
from urllib.parse import quote
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core import signals as astra_signals
from core.election_nominators import parse_nominator_identifier
from core.elections_eligibility import (
    CandidateValidationResult,
    ElectionEligibilityError,
    start_eligible_voters,
    validate_candidates_for_election,
)
from core.elections_timestamping import get_public_payload, schedule_attestation
from core.email_context import (
    election_committee_email_context,
    user_email_context,
    user_email_context_from_user,
)
from core.forms_elections import is_self_nomination
from core.freeipa.user import DegradedFreeIPAUser, FreeIPAUser
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.logging_extras import current_exception_log_fields
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    Organization,
    VotingCredential,
)
from core.public_urls import build_public_absolute_url
from core.templated_email import bulk_save_emails, queue_composed_email, queue_templated_email
from core.tokens import election_chain_next_hash, election_genesis_chain_hash

ELECTION_TALLY_ALGORITHM_NAME = "Meek STV (High-Precision Variant)"
ELECTION_TALLY_ALGORITHM_VERSION = "1.0"
ELECTION_TALLY_ALGORITHM_SPEC_DOC = "docs/runbooks/meek-stv-elections.md"
_MAX_RANKING_SIZE = 500

logger = logging.getLogger(__name__)

START_TRANSITION_CREDENTIAL_ISSUANCE_ERROR = (
    "Voting credentials can only be issued during election start (draft -> open)."
)


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

    audit_entry = AuditLogEntry.objects.create(
        election=locked,
        event_type="election_end_extended",
        payload=payload,
        is_public=True,
    )
    schedule_attestation(audit_entry)

    extended_election_id = locked.id
    previous_end_datetime = old_end
    new_end_datetime_value = new_end_datetime

    def _send_deadline_extended_signal() -> None:
        committed_election = Election.objects.get(pk=extended_election_id)
        astra_signals.election_deadline_extended.send(
            sender=Election,
            election=committed_election,
            actor=actor,
            previous_end_datetime=previous_end_datetime,
            new_end_datetime=new_end_datetime_value,
        )

    transaction.on_commit(_send_deadline_extended_signal)


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


def _election_email_context(*, election: Election, tz_name: str | None = None) -> dict[str, object]:
    """Shared election fields for email template contexts."""
    return {
        "election_id": election.id,
        "election_name": election.name,
        "election_description": election.description,
        "election_url": election.url,
        "election_start_datetime": _format_datetime_in_timezone(dt=election.start_datetime, tz_name=tz_name),
        "election_end_datetime": _format_datetime_in_timezone(dt=election.end_datetime, tz_name=tz_name),
        "election_number_of_seats": election.number_of_seats,
    }


def candidate_username_by_id_map(candidates: Iterable[Candidate]) -> dict[int, str]:
    """Build {candidate_id: username} from a candidate queryset, skipping blanks."""
    return {
        int(c.id): str(c.freeipa_username or "").strip()
        for c in candidates
        if str(c.freeipa_username or "").strip()
    }


def build_public_ballots_export(*, election: Election) -> dict[str, object]:
    candidates = Candidate.objects.filter(election=election).only("id", "freeipa_username")
    candidate_name_by_id = candidate_username_by_id_map(candidates)

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
        .exclude(event_type="quorum_reached")
        .only(
            "timestamp",
            "event_type",
            "payload",
            "rekor_log_id",
            "rekor_endpoint",
            "rekor_log_index",
            "rekor_integrated_time",
            "rekor_message_digest_hex",
            "rekor_canonical_message_version",
        )
        .order_by("timestamp", "id")
    )

    audit_log: list[dict[str, object]] = []
    for entry in entries:
        event: dict[str, object] = {
            "timestamp": entry.timestamp.date().isoformat(),
            "event_type": str(entry.event_type),
            "payload": get_public_payload(entry),
        }

        if entry.rekor_log_id:
            event["timestamping"] = {
                "version": 1,
                "rekor_log_id": entry.rekor_log_id,
                "rekor_log_index": entry.rekor_log_index,
                "rekor_integrated_time": (
                    entry.rekor_integrated_time.isoformat().replace("+00:00", "Z")
                    if entry.rekor_integrated_time
                    else None
                ),
                "rekor_entry_url": f"{entry.rekor_endpoint}/api/v1/log/entries/{entry.rekor_log_id}",
                "message_digest_hex": entry.rekor_message_digest_hex,
                "canonical_message_version": entry.rekor_canonical_message_version,
            }

        audit_log.append(event)

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
    if not ranking:
        raise InvalidBallotError("Invalid ballot: ranking is required")

    if len(ranking) > _MAX_RANKING_SIZE:
        raise InvalidBallotError(f"Ranking too long ({len(ranking)} entries; max {_MAX_RANKING_SIZE}).")

    allowed = set(
        Candidate.objects.filter(election=election).values_list(
            "id",
            flat=True,
        )
    )

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
    return build_public_absolute_url(rel, on_missing="relative")


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
    return build_public_absolute_url(rel, on_missing="relative")


def send_vote_receipt_email(
    *,
    request: HttpRequest | None,
    election: Election,
    username: str,
    email: str,
    receipt: BallotReceipt,
    tz_name: str | None = None,
    user: FreeIPAUser | DegradedFreeIPAUser | None = None,
) -> None:
    # election_vote_submit passes user=receipt_user for this path, so this
    # fallback is a defensive guard for other callers that only provide username.
    if user is None:
        recipient_context = user_email_context(username=username)
    else:
        normalized_username = str(user.username or "").strip()
        full_name = str(user.get_full_name() or "").strip()
        recipient_context = {
            "username": normalized_username,
            "first_name": str(user.first_name or ""),
            "last_name": str(user.last_name or ""),
            "full_name": full_name or normalized_username,
            "email": str(user.email or ""),
        }

    context: dict[str, object] = {
        **recipient_context,
        **election_committee_email_context(),
        **_election_email_context(election=election, tz_name=tz_name),
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
    include_credentials: bool = True,
    commit: bool = True,
) -> Email | None:
    context = build_voting_credential_email_context(
        request=request,
        election=election,
        username=username,
        credential_public_id=credential_public_id,
        tz_name=tz_name,
    )

    if not include_credentials:
        context.pop("credential_public_id", None)
        context.pop("vote_url_with_credential_fragment", None)

    if subject_template is not None or html_template is not None or text_template is not None:
        queued_email = queue_composed_email(
            recipients=[email],
            sender=settings.DEFAULT_FROM_EMAIL,
            subject_source=subject_template or "",
            html_source=html_template or "",
            text_source=text_template or "",
            context=context,
            reply_to=[settings.ELECTION_COMMITTEE_EMAIL],
            commit=commit,
        )
        queued_email.context = _post_office_json_context({"election_id": election.id})
        if commit:
            queued_email.save(update_fields=["context"])
        return queued_email

    context = _post_office_json_context(context)
    queued_email = queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        context=context,
        reply_to=[settings.ELECTION_COMMITTEE_EMAIL],
        commit=commit,
    )
    return queued_email


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
        **_election_email_context(election=election, tz_name=tz_name),
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
        eligible = start_eligible_voters(election=election)
        eligible_voter_count = len(eligible)
        eligible_vote_weight_total = sum(v.weight for v in eligible)

    ballot_agg = Ballot.objects.for_election(election=election).final().aggregate(
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
    """Record a voter's ranking for an election.

    Status enforcement: the election row is fetched under SELECT FOR UPDATE so
    that a concurrent `close_election()` call cannot race past this check.  Any
    status other than ``open`` raises `ElectionNotOpenError` and no Ballot row
    is created or modified.

    Receipt / coercion-resistance note:
    The returned `BallotReceipt` contains `(ballot_hash, nonce, chain_hash)`.  A
    voter who discloses their ranking, credential public ID, and nonce to a third
    party allows that party to recompute the hash and confirm consistency with the
    published receipt.  This provides *inclusion verifiability*, not coercion
    resistance: receipts can be used to prove how a specific ballot was ranked.
    The system does not implement mechanisms to deny or obscure a submitted ranking.
    """
    election = Election.objects.select_for_update().get(pk=election.pk)
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

    last_chain_hash = Ballot.objects.latest_chain_head_hash_for_election(election=election)
    genesis_hash = election_genesis_chain_hash(election.id)
    previous_chain_hash = str(last_chain_hash or genesis_hash)
    chain_hash = election_chain_next_hash(previous_chain_hash=previous_chain_hash, ballot_hash=ballot_hash)

    current = (
        Ballot.objects.select_for_update()
        .for_election(election=election)
        .final()
        .filter(credential_public_id=credential_public_id)
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

    def _evaluate_quorum_after_commit() -> None:
        try:
            committed_election = Election.objects.only("id", "status", "quorum").get(pk=election.id)
            status = election_quorum_status(election=committed_election)
            required_participating_voter_count = int(status["required_participating_voter_count"])
            required_participating_vote_weight_total = int(status["required_participating_vote_weight_total"])
            quorum_met = bool(status["quorum_met"])
            if required_participating_voter_count and required_participating_vote_weight_total and quorum_met:
                quorum_entry, created = AuditLogEntry.objects.get_or_create(
                    election=committed_election,
                    event_type="quorum_reached",
                    defaults={"payload": status, "is_public": True},
                )
                if created or not quorum_entry.rekor_log_id:
                    schedule_attestation(quorum_entry)
                if created:
                    astra_signals.election_quorum_met.send(
                        sender=Election,
                        election=committed_election,
                    )
        except Exception:
            logger.exception(
                "Deferred quorum evaluation failed for election_id=%s",
                election.id,
                extra=current_exception_log_fields(),
            )

    transaction.on_commit(_evaluate_quorum_after_commit)

    return BallotReceipt(
        ballot=ballot,
        nonce=nonce,
    )


@transaction.atomic
def _issue_voting_credential(
    *,
    election: Election,
    freeipa_username: str,
    weight: int,
) -> VotingCredential:
    if not freeipa_username.strip():
        raise ElectionError("freeipa_username is required")
    if weight <= 0:
        raise ElectionError("weight must be positive")
    if election.status != Election.Status.open:
        raise ElectionError("cannot issue credentials unless election is open")

    try:
        credential = VotingCredential.objects.select_for_update().get(
            election=election,
            freeipa_username=freeipa_username,
        )
    except VotingCredential.DoesNotExist:
        credential = None

    if credential is not None:
        if credential.weight != weight:
            logger.warning(
                "Election %s credential weight is immutable after issuance: username=%s existing=%s requested=%s",
                election.id,
                freeipa_username,
                credential.weight,
                weight,
            )
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
                logger.warning(
                    "Election %s credential weight is immutable after issuance: username=%s existing=%s requested=%s",
                    election.id,
                    freeipa_username,
                    credential.weight,
                    weight,
                )
            return credential


# Stable, non-sensitive list of what anonymize_election() scrubs.
# Included verbatim in the election_anonymized AuditLogEntry so auditors
# can determine which fields were removed without reading source code.
_ANONYMIZE_SCRUBBED_FIELDS: list[str] = [
    # Username-to-credential mapping removed for all election credentials.
    "VotingCredential.freeipa_username",
    # Credential delivery and vote-receipt emails deleted from mail store.
    "Email:election_credentials_and_receipts",
]


@transaction.atomic
def anonymize_election(*, election: Election) -> dict[str, int]:
    """Anonymize election credentials and scrub sensitive emails at close time.

    What IS scrubbed:
    - ``VotingCredential.freeipa_username``: set to NULL for every credential
      associated with the election.  The credential row itself is retained to
      preserve chain-of-custody for the credential public ID.
    - Election-related emails: deleted from the mail store.  This covers
      credential-delivery emails (matched by ``context["election_id"]``) and
      legacy composed credential emails (matched by vote-path + credential
      fragment in message body).

    What is NOT scrubbed:
    - ``VotingCredential`` rows (public_id, weight) — retained.
    - ``Ballot`` rows (ranking, hashes, chain) — retained for audit/tally.
    - ``AuditLogEntry`` records — retained in full.

    The ``election_anonymized`` AuditLogEntry (non-public) records affected counts
    and the ``scrubbed_fields`` list so auditors can verify completeness.

    Returns a dict with ``credentials_affected`` and ``emails_scrubbed`` counts.
    """
    if election.status not in {Election.Status.closed, Election.Status.tallied}:
        raise ElectionNotClosedError("election must be closed or tallied to anonymize")

    credentials_affected = VotingCredential.objects.filter(election=election).count()
    VotingCredential.objects.filter(
        election=election,
        freeipa_username__isnull=False,
    ).update(freeipa_username=None)

    emails_scrubbed = scrub_election_emails(election=election)

    scrub_anomaly = emails_scrubbed < credentials_affected
    if scrub_anomaly:
        logger.warning(
            "Election %s: emails_scrubbed=%d < credentials_affected=%d — possible scrub anomaly",
            election.id,
            emails_scrubbed,
            credentials_affected,
        )

    AuditLogEntry.objects.create(
        election=election,
        event_type="election_anonymized",
        payload={
            "credentials_affected": credentials_affected,
            "emails_scrubbed": emails_scrubbed,
            "scrub_anomaly": scrub_anomaly,
            "scrubbed_fields": _ANONYMIZE_SCRUBBED_FIELDS,
        },
        is_public=False,
    )

    return {"credentials_affected": credentials_affected, "emails_scrubbed": emails_scrubbed}


@transaction.atomic
def _issue_voting_credentials_from_memberships(
    *,
    election: Election,
) -> list[VotingCredential]:
    if election.status != Election.Status.open:
        raise ElectionError("cannot issue credentials unless election is open")

    # Guard once — this is the only check VotingCredential.save() performs, so
    # we can safely bypass per-row save() and use bulk_create() below.
    if AuditLogEntry.objects.filter(election=election, event_type="election_anonymized").exists():
        raise ElectionError("cannot issue credentials for an anonymized election")

    eligible = start_eligible_voters(
        election=election,
        require_fresh=True,
    )

    # Re-issue guard: if any credentials already exist (e.g. partial retry),
    # fall back to the safe per-row path that handles duplicates.
    existing_count = VotingCredential.objects.filter(election=election).count()
    if existing_count > 0:
        issued: list[VotingCredential] = []
        for voter in eligible:
            credential = _issue_voting_credential(
                election=election,
                freeipa_username=voter.username,
                weight=voter.weight,
            )
            issued.append(credential)
        return issued

    # Fast path: no existing credentials, create in bulk.
    objs = [
        VotingCredential(
            election=election,
            public_id=VotingCredential.generate_public_id(),
            freeipa_username=voter.username,
            weight=voter.weight,
        )
        for voter in eligible
    ]
    return VotingCredential.objects.bulk_create(objs, batch_size=500)


@transaction.atomic
def issue_credentials_at_start_transition(*, election: Election) -> list[VotingCredential]:
    """Issue credentials for eligible voters as part of election start transition.

    This is the only public issuance entry point. Keeping issuance behind a
    single transition-named API prevents callers from bypassing lifecycle policy
    via intent flags.
    """
    if election.status != Election.Status.open:
        raise ElectionError(START_TRANSITION_CREDENTIAL_ISSUANCE_ERROR)

    credentials = _issue_voting_credentials_from_memberships(election=election)
    _populate_election_roll(election=election)
    return credentials


def _populate_election_roll(*, election: Election) -> None:
    """Copy eligible usernames from VotingCredential into ElectionRoll.

    Uses INSERT ... SELECT with ORDER BY RANDOM() so the roll row order
    cannot be correlated with the credential table order.
    """
    from core.models import ElectionRoll

    ElectionRoll.objects.filter(election=election).delete()

    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core_electionroll (election_id, freeipa_username)
            SELECT election_id, freeipa_username
              FROM core_votingcredential
             WHERE election_id = %s
               AND freeipa_username IS NOT NULL
             ORDER BY RANDOM()
            """,
            [election.pk],
        )


@transaction.atomic
def scrub_election_emails(*, election: Election) -> int:
    """Delete sensitive emails associated with an election from the mail store.

    Two categories are removed:
    1. **Context-keyed emails** — any ``Email`` whose ``context`` JSON includes
       ``{"election_id": <election.id>}``.  This covers credential-delivery and
       vote-receipt emails created by the current implementation.
    2. **Legacy composed emails** — emails whose ``message`` or ``html_message``
       body contains both the election vote-URL path and the ``#credential=``
       fragment.  This covers credential emails composed before the context-keyed
       approach was introduced.

    Returns the count of deleted ``Email`` rows.
    """
    vote_path = reverse("election-vote", args=[election.id])
    credential_fragment = "#credential="

    legacy_composed_credential_match = (
        (Q(message__contains=vote_path) | Q(html_message__contains=vote_path))
        & (Q(message__contains=credential_fragment) | Q(html_message__contains=credential_fragment))
    )

    count, _ = Email.objects.filter(
        Q(context__contains={"election_id": election.id})
        | legacy_composed_credential_match
    ).delete()
    return count


def close_election(
    *,
    election: Election,
    actor: str | None = None,
    scheduled_for: datetime.datetime | None = None,
) -> None:
    """Close an open election, anonymize credentials, and record a public audit event.

    Sequence (all within a single transaction):
    1. Lock the election row (SELECT FOR UPDATE) and verify status is ``open``.
    2. Set ``status = closed`` and ``end_datetime = now``.
    3. Call :func:`anonymize_election`, which:
       - Nulls ``VotingCredential.freeipa_username`` for all election credentials.
       - Deletes credential-delivery and vote-receipt emails from the mail store.
       - Records a private ``election_anonymized`` AuditLogEntry with affected counts
         and ``scrubbed_fields``.
    4. Create a public ``election_closed`` AuditLogEntry with ``chain_head``,
       ``credentials_affected``, and ``emails_scrubbed`` counts.

    Lifecycle invariant: when an election closes, credential rows are retained for
    chain-of-custody but anonymized by setting ``freeipa_username = NULL``.
    """
    try:
        with transaction.atomic():
            election = Election.objects.select_for_update().get(pk=election.pk)
            if election.status != Election.Status.open:
                raise ElectionError("election must be open to close")

            ended_at = timezone.now()
            last_chain_hash = Ballot.objects.latest_chain_head_hash_for_election(election=election)
            genesis_hash = election_genesis_chain_hash(election.id)
            chain_head = str(last_chain_hash or genesis_hash)

            election.status = Election.Status.closed
            if scheduled_for is None:
                election.end_datetime = ended_at
                election.save(update_fields=["status", "end_datetime"])
            else:
                election.save(update_fields=["status"])

            anonymize = anonymize_election(election=election)

            payload = {
                "chain_head": chain_head,
                "credentials_affected": anonymize["credentials_affected"],
                "emails_scrubbed": anonymize["emails_scrubbed"],
            }
            if actor:
                payload["actor"] = actor
            if scheduled_for is not None:
                payload["automation"] = True
                payload["scheduled_for"] = scheduled_for.isoformat()
                payload["transitioned_at"] = ended_at.isoformat()

            audit_entry = AuditLogEntry.objects.create(
                election=election,
                event_type="election_closed",
                payload=payload,
                is_public=True,
            )
            schedule_attestation(audit_entry)

            closed_election_id = election.id

            def _send_closed_signal() -> None:
                committed_election = Election.objects.get(pk=closed_election_id)
                astra_signals.election_closed.send(
                    sender=Election,
                    election=committed_election,
                    actor=actor,
                )

            transaction.on_commit(_send_closed_signal)
    except ElectionError:
        raise
    except Exception as exc:
        failure_payload: dict[str, object] = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if actor:
            failure_payload["actor"] = actor

        try:
            AuditLogEntry.objects.create(
                election=election,
                event_type="election_close_failed",
                payload=failure_payload,
                is_public=False,
            )
        except Exception:
            pass  # Don't let audit log failure mask original error

        raise ElectionError(
            "Failed to close election. "
            "Recovery: Verify database connectivity and election state, then retry. "
            "Contact an administrator if the issue persists"
            f": {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ElectionStartOpenResult:
    """Outcome of the draft -> open half of an election start."""

    status: str
    reasons: tuple[str, ...] = ()
    credential_count: int = 0
    opened_at: datetime.datetime | None = None


@dataclass(frozen=True, slots=True)
class ElectionStartPreview:
    """Facts an operator confirms before starting an election."""

    election_name: str
    number_of_seats: int
    candidate_count: int
    eligible_voter_count: int


@dataclass(frozen=True, slots=True)
class ElectionStartDelivery:
    """Running totals for credential email delivery during an election start."""

    total: int = 0
    processed: int = 0
    emailed: int = 0
    skipped: int = 0
    failures: int = 0


# Credential emails are rendered and persisted in batches so that delivery
# progress is observable (and memory bounded) for a large electorate. Progress
# is reported once per batch, so this also sets how finely the operator's
# progress bar advances -- it can never be finer than what has actually been
# written to the mail queue.
ELECTION_START_EMAIL_BATCH_SIZE = 10


def election_start_preview(*, election: Election) -> ElectionStartPreview:
    """Summarize an election for the start confirmation dialog.

    Uses the cached electorate: this only informs the operator, while
    ``open_election_for_start`` re-reads eligibility fresh before committing.
    """
    return ElectionStartPreview(
        election_name=election.name,
        number_of_seats=election.number_of_seats,
        candidate_count=Candidate.objects.filter(election=election).count(),
        eligible_voter_count=len(start_eligible_voters(election=election)),
    )


def _ineligible_nominator_labels(nominators: Iterable[str]) -> list[str]:
    """Name ineligible nominators without leaking raw organization identifiers."""
    parsed = [(nominator, parse_nominator_identifier(nominator).organization_id) for nominator in nominators]
    organization_names = {
        organization.id: organization.name
        for organization in Organization.objects.filter(
            pk__in={organization_id for _, organization_id in parsed if organization_id is not None}
        ).only("id", "name")
    }
    return [
        nominator if organization_id is None else organization_names.get(organization_id, "organization nominator")
        for nominator, organization_id in parsed
    ]


def _start_validation_reasons(
    *,
    validation: CandidateValidationResult,
    has_eligible_voters: bool,
) -> list[str]:
    """Build one operator-facing message per blocking eligibility problem."""
    reasons: list[str] = []
    if validation.disqualified_candidates:
        names = ", ".join(sorted(validation.disqualified_candidates, key=str.lower))
        reasons.append("Election committee members cannot be candidates for this election: " + names)
    if validation.disqualified_nominators:
        names = ", ".join(sorted(validation.disqualified_nominators, key=str.lower))
        reasons.append("Election committee members cannot nominate candidates for this election: " + names)
    if validation.ineligible_candidates:
        names = ", ".join(sorted(validation.ineligible_candidates, key=str.lower))
        reasons.append("Candidate is not eligible: " + names)
    if validation.ineligible_nominators:
        names = ", ".join(_ineligible_nominator_labels(sorted(validation.ineligible_nominators, key=str.lower)))
        reasons.append("One or more nominators are not eligible for this election: " + names)
    if not has_eligible_voters:
        reasons.append("No eligible voters were found for this election.")
    return reasons


@transaction.atomic
def open_election_for_start(*, election_id: int, scheduled: bool = True) -> ElectionStartOpenResult:
    """Validate a draft election, issue its voting credentials and open it.

    Credential emails are deliberately *not* queued here: delivery takes one
    rendered email per voter and must run outside this transaction so that
    ``complete_election_start`` can report progress to concurrent readers.
    """
    election = Election.objects.select_for_update().get(pk=election_id)
    now = timezone.now()
    if election.status != Election.Status.draft:
        return ElectionStartOpenResult(status="skipped")
    if scheduled and (not election.auto_start_enabled or election.start_datetime > now):
        return ElectionStartOpenResult(status="skipped")

    candidates = list(Candidate.objects.filter(election=election).only("freeipa_username", "nominated_by"))
    if not candidates:
        return ElectionStartOpenResult(
            status="invalid",
            reasons=("Add at least one candidate before starting the election.",),
        )

    self_nominations = sorted(
        {
            str(candidate.freeipa_username or "").strip()
            for candidate in candidates
            if is_self_nomination(
                candidate_username=candidate.freeipa_username,
                nominator_username=candidate.nominated_by,
            )
        },
        key=str.lower,
    )
    if self_nominations:
        return ElectionStartOpenResult(
            status="invalid",
            reasons=("Candidates cannot nominate themselves: " + ", ".join(self_nominations),),
        )

    candidate_usernames = [str(candidate.freeipa_username or "").strip() for candidate in candidates]
    nominator_usernames = [str(candidate.nominated_by or "").strip() for candidate in candidates]
    try:
        eligible_voters = start_eligible_voters(election=election, require_fresh=True)
        validation = validate_candidates_for_election(
            election=election,
            candidate_usernames=candidate_usernames,
            nominator_usernames=nominator_usernames,
            eligible_group_cn=str(election.eligible_group_cn or "").strip(),
            require_fresh=True,
        )
    except ElectionEligibilityError as exc:
        return ElectionStartOpenResult(status="invalid", reasons=(str(exc),))

    reasons = _start_validation_reasons(validation=validation, has_eligible_voters=bool(eligible_voters))
    if reasons:
        return ElectionStartOpenResult(status="invalid", reasons=tuple(reasons))

    credentials = issue_credentials_at_start_transition_for_scheduled_election(election=election)
    if not scheduled:
        election.start_datetime = now
    election.status = Election.Status.open
    election.auto_start_enabled = False
    election.save(update_fields=["status", "auto_start_enabled", "start_datetime", "updated_at"])
    return ElectionStartOpenResult(status="opened", credential_count=len(credentials), opened_at=now)


def deliver_start_credential_emails(
    *,
    election: Election,
    credentials: Sequence[VotingCredential],
    request: HttpRequest | None = None,
    on_progress: Callable[[ElectionStartDelivery], None] | None = None,
) -> ElectionStartDelivery:
    """Queue one voting credential email per issued credential.

    Voters without a resolvable FreeIPA account or email address are skipped and
    rendering errors are counted as failures, so a few bad addresses never abort
    a start; the operator re-sends those from the election page afterwards.
    """
    total = len(credentials)
    processed = 0
    emailed = 0
    skipped = 0
    failures = 0

    # Pre-warm individual FreeIPA user cache entries from the all-users list so
    # that the per-voter get() calls below are instant cache hits instead of N
    # individual IPA RPC calls.
    FreeIPAUser.warm_user_cache(
        [str(credential.freeipa_username or "").strip() for credential in credentials if credential.freeipa_username]
    )

    use_snapshot = bool(
        election.voting_email_subject.strip()
        or election.voting_email_html.strip()
        or election.voting_email_text.strip()
    )

    for batch in batched(credentials, ELECTION_START_EMAIL_BATCH_SIZE):
        pending_emails: list[Email] = []
        for credential in batch:
            processed += 1
            username = str(credential.freeipa_username or "").strip()
            if not username:
                skipped += 1
                continue
            try:
                user = FreeIPAUser.get(username, respect_privacy=False)
                if user is None or not user.email:
                    skipped += 1
                    continue
                queued = send_voting_credential_email(
                    request=request,
                    election=election,
                    username=username,
                    email=user.email,
                    credential_public_id=str(credential.public_id),
                    tz_name=_get_freeipa_timezone_name(user),
                    subject_template=election.voting_email_subject if use_snapshot else None,
                    html_template=election.voting_email_html if use_snapshot else None,
                    text_template=election.voting_email_text if use_snapshot else None,
                    commit=False,
                )
            except Exception:
                logger.exception(
                    "election start: credential email failed election_id=%s username=%s",
                    election.id,
                    username,
                )
                failures += 1
                continue
            if queued is not None:
                pending_emails.append(queued)
            emailed += 1

        bulk_save_emails(pending_emails)
        if on_progress is not None:
            on_progress(
                ElectionStartDelivery(
                    total=total,
                    processed=processed,
                    emailed=emailed,
                    skipped=skipped,
                    failures=failures,
                )
            )

    return ElectionStartDelivery(
        total=total,
        processed=processed,
        emailed=emailed,
        skipped=skipped,
        failures=failures,
    )


def complete_election_start(
    *,
    election_id: int,
    scheduled: bool,
    opened_at: datetime.datetime,
    actor: str = "",
    on_progress: Callable[[ElectionStartDelivery], None] | None = None,
) -> ElectionStartDelivery:
    """Deliver credential emails for a just-opened election and record the start.

    Runs outside the opening transaction so delivery progress is visible to
    other requests while it is still going.
    """
    election = Election.objects.get(pk=election_id)
    credentials = list(VotingCredential.objects.filter(election=election).only("public_id", "freeipa_username"))
    delivery = deliver_start_credential_emails(
        election=election,
        credentials=credentials,
        on_progress=on_progress,
    )

    with transaction.atomic():
        audit_entry = AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload={
                "eligible_voters": delivery.total,
                "emailed": delivery.emailed,
                "skipped": delivery.skipped,
                "failures": delivery.failures,
                "genesis_chain_hash": election_genesis_chain_hash(election.id),
                "candidates": [
                    {"id": candidate.id, "freeipa_username": candidate.freeipa_username, "tiebreak_uuid": str(candidate.tiebreak_uuid)}
                    for candidate in Candidate.objects.filter(election=election).only("id", "freeipa_username", "tiebreak_uuid")
                ],
                "automation": scheduled,
                "actor": actor,
                "scheduled_for": election.start_datetime.isoformat() if scheduled else None,
                "transitioned_at": opened_at.isoformat(),
            },
            is_public=True,
        )
        schedule_attestation(audit_entry)
        transaction.on_commit(
            lambda: astra_signals.election_opened.send(
                sender=Election,
                election=Election.objects.get(pk=election.id),
                actor=actor or None,
            )
        )
    return delivery


def start_scheduled_election(*, election_id: int, scheduled: bool = True) -> dict[str, int | str]:
    """Open one due opted-in draft election and queue its credential email."""
    opened = open_election_for_start(election_id=election_id, scheduled=scheduled)
    if opened.status != "opened":
        result: dict[str, int | str] = {"status": opened.status}
        if opened.reasons:
            result["reason"] = "; ".join(opened.reasons)
        return result

    delivery = complete_election_start(
        election_id=election_id,
        scheduled=scheduled,
        opened_at=opened.opened_at,
        actor="operations_hourly" if scheduled else "",
    )
    return {
        "status": "started",
        "emailed": delivery.emailed,
        "skipped": delivery.skipped,
        "failures": delivery.failures,
    }


def issue_credentials_at_start_transition_for_scheduled_election(*, election: Election) -> list[VotingCredential]:
    election.status = Election.Status.open
    election.save(update_fields=["status"])
    try:
        return issue_credentials_at_start_transition(election=election)
    finally:
        election.status = Election.Status.draft


def tally_election(*, election: Election, actor: str | None = None) -> dict[str, object]:
    from core.elections_meek import MEEK_DEFAULT_EPSILON, MEEK_DEFAULT_MAX_ITERATIONS, tally_meek
    from core.models import ExclusionGroup, ExclusionGroupCandidate

    try:
        with transaction.atomic():
            election = Election.objects.select_for_update().get(pk=election.pk)
            if election.status != Election.Status.closed:
                raise ElectionError("Only closed elections can be tallied.")

            candidates_qs = Candidate.objects.filter(election=election).only(
                "id",
                "freeipa_username",
                "tiebreak_uuid",
            )
            candidates: list[dict[str, object]] = [
                {"id": c.id, "name": c.freeipa_username, "tiebreak_uuid": c.tiebreak_uuid} for c in candidates_qs
            ]

            ballots_qs = Ballot.objects.for_election(election=election).final().only("weight", "ranking")
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
                epsilon=MEEK_DEFAULT_EPSILON,
                max_iterations=MEEK_DEFAULT_MAX_ITERATIONS,
            )
            result = _jsonify_tally_result(raw_result)

            result["algorithm"] = {
                "name": ELECTION_TALLY_ALGORITHM_NAME,
                "version": ELECTION_TALLY_ALGORITHM_VERSION,
                "specification": {
                    "doc": ELECTION_TALLY_ALGORITHM_SPEC_DOC,
                    "url_path": reverse("election-algorithm"),
                },
                # Exact convergence parameters used — persisted for reproducibility.
                "epsilon": str(MEEK_DEFAULT_EPSILON),
                "max_iterations": MEEK_DEFAULT_MAX_ITERATIONS,
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
                "algorithm": result.get("algorithm"),
            }
            if actor:
                tally_completed_payload["actor"] = actor

            tally_completed_entry = AuditLogEntry.objects.create(
                election=election,
                event_type="tally_completed",
                payload=tally_completed_payload,
                is_public=True,
            )
            schedule_attestation(tally_completed_entry)

            tallied_election_id = election.id

            def _send_tallied_signal() -> None:
                committed_election = Election.objects.get(pk=tallied_election_id)
                astra_signals.election_tallied.send(
                    sender=Election,
                    election=committed_election,
                    actor=actor,
                )

            transaction.on_commit(_send_tallied_signal)

            return result
    except ElectionError:
        raise
    except Exception as exc:
        failure_payload: dict[str, object] = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if actor:
            failure_payload["actor"] = actor

        try:
            AuditLogEntry.objects.create(
                election=election,
                event_type="tally_failed",
                payload=failure_payload,
                is_public=False,
            )
        except Exception:
            pass  # Don't let audit log failure mask original error

        raise ElectionError(
            "Failed to tally election. "
            "Recovery: Review the ballot data integrity and candidate configuration, then retry from the election detail page. "
            "The election remains in the closed state and can be tallied again. "
            "Contact an administrator if the problem persists. "
            f"Details: {exc}"
        ) from exc
