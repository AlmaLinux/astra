"""Election voting: ballot parsing, vote submission, and vote page."""

import hmac
import json
import random

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import elections_services
from core.backends import FreeIPAUser
from core.elections_services import (
    ElectionNotOpenError,
    InvalidBallotError,
    InvalidCredentialError,
    submit_ballot,
)
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.models import Candidate, Election, VotingCredential
from core.rate_limit import allow_request
from core.views_elections._helpers import _get_active_election, _load_candidate_users
from core.views_utils import block_action_without_coc, get_username, has_signed_coc


def _parse_vote_payload(request, *, election: Election) -> tuple[str, list[int]]:
    if request.content_type and request.content_type.startswith("application/json"):
        raw = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(raw)
        credential_public_id = str(data.get("credential_public_id") or "").strip()
        ranking_raw = data.get("ranking")
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

    # Voting eligibility and weight are determined when credentials are issued.
    # Do not re-check current memberships here; they can change while the election is open.
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

    try:
        receipt = submit_ballot(
            election=election,
            credential_public_id=credential_public_id,
            ranking=ranking,
        )
    except (InvalidBallotError, InvalidCredentialError, ElectionNotOpenError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    freeipa_user = FreeIPAUser.get(username)
    voter_email = str(freeipa_user.email or "").strip() if freeipa_user is not None else ""
    if voter_email:
        tz_name = _get_freeipa_timezone_name(freeipa_user) if freeipa_user is not None else None
        elections_services.send_vote_receipt_email(
            request=request,
            election=election,
            username=username,
            email=voter_email,
            receipt=receipt,
            tz_name=tz_name,
        )

    return JsonResponse(
        {
            "ok": True,
            "election_id": election.id,
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
        blocked = block_action_without_coc(
            request,
            username=username,
            action_label="vote in elections",
        )
        if blocked is not None:
            return blocked

    voter_votes: int | None = None
    if username:
        credential = (
            VotingCredential.objects.filter(election=election, freeipa_username=username)
            .only("weight")
            .first()
        )
        voter_votes = int(credential.weight or 0) if credential is not None else 0

    can_submit_vote = voter_votes is not None and voter_votes > 0

    candidates = list(Candidate.objects.filter(election=election))
    random.shuffle(candidates)

    users_by_username = _load_candidate_users({c.freeipa_username for c in candidates if c.freeipa_username})

    candidate_display: list[dict[str, object]] = []
    for c in candidates:
        user = users_by_username.get(c.freeipa_username)
        full_name = user.full_name if user is not None else c.freeipa_username
        label = f"{full_name} ({c.freeipa_username})"
        candidate_display.append({"candidate": c, "label": label})

    return render(
        request,
        "core/election_vote.html",
        {
            "election": election,
            "candidates": candidate_display,
            "voter_votes": voter_votes,
            "can_submit_vote": can_submit_vote,
        },
    )
