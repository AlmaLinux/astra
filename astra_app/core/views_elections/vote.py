"""Election voting: ballot parsing, vote submission, and vote page."""

import hmac
import json
import logging
import random
from typing import cast

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import elections_services
from core.elections_eligibility import VoteWeightLine, vote_weight_breakdown_for_username
from core.elections_services import (
    ElectionNotOpenError,
    InvalidBallotError,
    InvalidCredentialError,
    submit_ballot,
)
from core.freeipa.user import DegradedFreeIPAUser, FreeIPAUser
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.logging_extras import current_exception_log_fields
from core.models import Candidate, Election, Membership, VotingCredential
from core.rate_limit import allow_request
from core.views_elections._helpers import _get_active_election, _load_candidate_users
from core.views_utils import block_action_without_coc, get_username, has_signed_coc

logger = logging.getLogger(__name__)


def _candidate_vote_display(candidates: list[Candidate]) -> list[dict[str, object]]:
    users_by_username = _load_candidate_users({candidate.freeipa_username for candidate in candidates if candidate.freeipa_username})

    candidate_display: list[dict[str, object]] = []
    for candidate in candidates:
        user = users_by_username.get(candidate.freeipa_username)
        full_name = user.full_name if user is not None else candidate.freeipa_username
        label = f"{full_name} ({candidate.freeipa_username})"
        candidate_display.append({"candidate": candidate, "label": label})
    return candidate_display


def _election_vote_page_context(request, *, election: Election) -> dict[str, object]:
    username = get_username(request)

    if username:
        blocked = block_action_without_coc(
            request,
            username=username,
            action_label="vote in elections",
        )
        if blocked is not None:
            return {"blocked_response": blocked}

    voter_votes: int | None = None
    if username:
        credential = (
            VotingCredential.objects.filter(election=election, freeipa_username=username)
            .only("weight")
            .first()
        )
        voter_votes = int(credential.weight or 0) if credential is not None else 0

    voter_vote_breakdown = []
    if username and voter_votes is not None and voter_votes > 0:
        voter_vote_breakdown = vote_weight_breakdown_for_username(
            election=election,
            username=username,
        )

    can_submit_vote = voter_votes is not None and voter_votes > 0

    candidates = list(Candidate.objects.filter(election=election))
    random.shuffle(candidates)

    return {
        "election": election,
        "candidates": _candidate_vote_display(candidates),
        "voter_votes": voter_votes,
        "voter_vote_breakdown": voter_vote_breakdown,
        "can_submit_vote": can_submit_vote,
        "ballot_verify_url": reverse("ballot-verify"),
        "vote_submit_url": reverse("api-election-vote-submit", args=[election.id]),
    }


def _serialize_vote_weight_breakdown(lines: list[VoteWeightLine]) -> list[dict[str, object]]:
    return [
        {
            "votes": line.votes,
            "label": line.label,
            "org_name": line.org_name or None,
        }
        for line in lines
    ]


def _parse_vote_payload(request, *, election: Election) -> tuple[str, list[int]]:
    if request.content_type and request.content_type.startswith("application/json"):
        raw = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(raw)
        credential_public_id = str(data.get("credential_public_id") or "").strip()
        ranking_raw = data.get("ranking")
        ranking_usernames_raw = str(data.get("ranking_usernames") or "").strip()
        if not ranking_raw and ranking_usernames_raw:
            ranking_raw = ranking_usernames_raw
    else:
        credential_public_id = str(request.POST.get("credential_public_id") or "").strip()
        ranking_raw = str(request.POST.get("ranking") or "").strip()
        ranking_usernames_raw = str(request.POST.get("ranking_usernames") or "").strip()
        if not ranking_raw and ranking_usernames_raw:
            ranking_raw = ranking_usernames_raw

    if not credential_public_id:
        raise ValueError("credential_public_id is required")

    if isinstance(ranking_raw, list):
        ranking = [int(x) for x in ranking_raw]
    elif isinstance(ranking_raw, str):
        # Allow comma-separated input.
        parts = [p.strip() for p in ranking_raw.split(",") if p.strip()]
        if not parts:
            ranking = []
        else:
            # First try numeric IDs (JS path).
            try:
                ranking = [int(p) for p in parts]
            except ValueError:
                # No-JS fallback: accept comma-separated FreeIPA usernames.
                # Resolve them to candidate IDs at submit-time.
                ranking = []
                usernames = [p.lower() for p in parts]
                candidates = list(
                    Candidate.objects.filter(election=election, freeipa_username__in=usernames).values_list(
                        "freeipa_username",
                        "id",
                    )
                )
                by_username = {u.lower(): int(cid) for u, cid in candidates}
                unknown = sorted({u for u in usernames if u not in by_username})
                if unknown:
                    raise ValueError("Invalid ranking: contains usernames that are not candidates")

                for u in usernames:
                    ranking.append(by_username[u])
    else:
        raise ValueError("ranking must be a list")

    if not ranking:
        raise ValueError("ranking is required")

    return credential_public_id, ranking


@require_POST
def election_vote_submit(request, election_id: int):
    username = get_username(request)
    if not username:
        return JsonResponse({"ok": False, "error": "Authentication required."}, status=403)

    election = _get_active_election(election_id, fields=["id", "status"])

    try:
        credential_public_id, ranking = _parse_vote_payload(request, election=election)
    except (ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    if not has_signed_coc(username):
        return JsonResponse(
            {
                "ok": False,
                "error": f"You must sign the {settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN} before you can vote.",
            },
            status=403,
        )

    if not allow_request(
        scope="elections.vote_submit",
        key_parts=[str(election.id), username],
        limit=settings.ELECTION_RATE_LIMIT_VOTE_SUBMIT_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_VOTE_SUBMIT_WINDOW_SECONDS,
    ):
        return JsonResponse(
            {"ok": False, "error": "Too many vote submissions. Please try again later."},
            status=429,
        )

    # VotingCredential confirms eligibility at issuance; we also re-verify active
    # membership here to prevent revoked users from voting.
    try:
        user_credential = VotingCredential.objects.only("public_id", "weight").get(
            election_id=election.id,
            freeipa_username=username,
        )
    except VotingCredential.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Not eligible to vote in this election."}, status=403)

    if int(user_credential.weight or 0) <= 0:
        return JsonResponse({"ok": False, "error": "Not eligible to vote in this election."}, status=403)

    if not hmac.compare_digest(str(user_credential.public_id), str(credential_public_id)):
        return JsonResponse({"ok": False, "error": "Invalid credential."}, status=400)

    has_individual = Membership.objects.filter(
        target_username__iexact=username,
        membership_type__votes__gt=0,
        membership_type__enabled=True,
    ).active().exists()
    has_org = Membership.objects.filter(
        target_organization__representative__iexact=username,
        membership_type__votes__gt=0,
        membership_type__enabled=True,
    ).active().exists()
    if not has_individual and not has_org:
        return JsonResponse(
            {"ok": False, "error": "Your membership is no longer active. You are not eligible to vote."},
            status=403,
        )

    try:
        receipt = submit_ballot(
            election=election,
            credential_public_id=credential_public_id,
            ranking=ranking,
        )
    except (InvalidBallotError, InvalidCredentialError, ElectionNotOpenError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    request_user = request.user
    receipt_user: FreeIPAUser | DegradedFreeIPAUser | None = None
    if isinstance(request_user, (FreeIPAUser, DegradedFreeIPAUser)):
        receipt_user = request_user

    voter_email = str(receipt_user.email or "").strip() if receipt_user is not None else ""
    email_queued = False
    if voter_email:
        tz_name = _get_freeipa_timezone_name(receipt_user) if isinstance(receipt_user, FreeIPAUser) else None
        try:
            elections_services.send_vote_receipt_email(
                request=request,
                election=election,
                username=username,
                email=voter_email,
                receipt=receipt,
                tz_name=tz_name,
                user=receipt_user,
            )
            email_queued = True
        except Exception:
            logger.exception(
                "send_vote_receipt_email failed for election_id=%s username=%s",
                election.id,
                username,
                extra=current_exception_log_fields(),
            )

    return JsonResponse(
        {
            "ok": True,
            "election_id": election.id,
            "email_queued": email_queued,
            "ballot_hash": receipt.ballot.ballot_hash,
            "nonce": receipt.nonce,
            "previous_chain_hash": receipt.ballot.previous_chain_hash,
            "chain_hash": receipt.ballot.chain_hash,
        }
    )


@require_GET
def election_vote(request, election_id: int):
    election = _get_active_election(election_id)
    if election.status in {Election.Status.closed, Election.Status.tallied}:
        return render(
            request,
            "core/election_vote_closed.html",
            {
                "election": election,
                "ballot_verify_url": reverse("ballot-verify"),
            },
            status=410,
        )
    if election.status != Election.Status.open:
        raise Http404

    username = get_username(request)
    if username:
        blocked_response = block_action_without_coc(
            request,
            username=username,
            action_label="vote in elections",
        )
        if blocked_response is not None:
            return blocked_response

    return render(
        request,
        "core/election_vote.html",
        {
            "election": election,
            "election_detail_url_template": reverse("election-detail", args=[123456789]).replace(
                "123456789", "__election_id__"
            ),
            "ballot_verify_url": reverse("ballot-verify"),
        },
    )


@require_GET
def election_vote_api(request, election_id: int):
    election = _get_active_election(election_id)
    if election.status != Election.Status.open:
        raise Http404

    context = _election_vote_page_context(request, election=election)
    blocked_response = context.pop("blocked_response", None)
    if blocked_response is not None:
        return blocked_response

    return JsonResponse(
        {
            "election": {
                "id": election.id,
                "name": election.name,
                "start_datetime": election.start_datetime.isoformat(),
                "end_datetime": election.end_datetime.isoformat(),
                "submit_url": context["vote_submit_url"],
                "can_submit_vote": context["can_submit_vote"],
                "voter_votes": context["voter_votes"],
            },
            "vote_weight_breakdown": _serialize_vote_weight_breakdown(
                cast(list[VoteWeightLine], context["voter_vote_breakdown"])
            ),
            "candidates": [
                {
                    "id": item["candidate"].id,
                    "username": item["candidate"].freeipa_username,
                    "label": item["label"],
                }
                for item in context["candidates"]
            ],
        }
    )
