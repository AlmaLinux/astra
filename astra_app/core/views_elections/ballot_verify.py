"""Ballot verification view."""

import re
from typing import Any

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from core.models import Ballot, Candidate, Election
from core.rate_limit import allow_request

_RECEIPT_RE = re.compile(r"^[0-9a-f]{64}$")


def _ballot_verify_context(request) -> tuple[dict[str, Any], int]:
    receipt_raw = str(request.GET.get("receipt") or "").strip()
    receipt = receipt_raw.lower()

    has_query = bool(receipt_raw)
    is_valid_receipt = bool(_RECEIPT_RE.fullmatch(receipt)) if receipt else False

    client_ip = str(request.META.get("REMOTE_ADDR") or "").strip() or "unknown"

    if has_query and not allow_request(
        scope="elections.ballot_verify",
        key_parts=[client_ip],
        limit=settings.ELECTION_RATE_LIMIT_BALLOT_VERIFY_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_BALLOT_VERIFY_WINDOW_SECONDS,
    ):
        return (
            {
                "receipt": receipt_raw,
                "has_query": has_query,
                "is_valid_receipt": is_valid_receipt,
                "found": False,
                "election": None,
                "election_status": "",
                "submitted_date": "",
                "is_superseded": False,
                "is_final_ballot": False,
                "public_ballots_url": "",
                "rate_limited": True,
                "verification_snippet": "",
            },
            429,
        )

    ballot: Ballot | None = None
    if is_valid_receipt:
        ballot = (
            Ballot.objects.select_related("election", "superseded_by")
            .only(
                "ballot_hash",
                "credential_public_id",
                "created_at",
                "is_counted",
                "superseded_by_id",
                "election__id",
                "election__name",
                "election__status",
                "election__public_ballots_file",
                "election__public_audit_file",
            )
            .filter(ballot_hash=receipt)
            .first()
        )

    found = ballot is not None
    election: Election | None = ballot.election if ballot is not None else None
    election_status = str(election.status) if election is not None else ""

    is_superseded = bool(ballot is not None and ballot.superseded_by_id)
    is_final_ballot = bool(found and not is_superseded)

    submitted_date = ballot.created_at.date().isoformat() if ballot is not None else ""

    has_public_verification = election is not None and election.status in {Election.Status.closed, Election.Status.tallied}
    public_ballots_url = ""
    if election is not None and has_public_verification:
        public_ballots_url = election.public_ballots_file.url if election.public_ballots_file else reverse(
            "election-public-ballots", args=[election.id]
        )

    verification_snippet = ""
    if found and election is not None and ballot is not None:
        candidate_rows = Candidate.objects.filter(election=election).values_list("freeipa_username", "id")
        candidate_ids_by_username = {str(username): int(cid) for username, cid in candidate_rows}

        lines: list[str] = [
            "# Copy/paste these values into verify-ballot-hash.py",
            f"election_id = {election.id}",
            f"# Locate your voting credential in the first email you received, titled 'Your voting credential for {election.name}'.",
            "candidate_ids_by_username = {",
        ]
        for username in sorted(candidate_ids_by_username.keys(), key=str.lower):
            cid = candidate_ids_by_username[username]
            lines.append(f'    "{username}": {cid},')
        lines.append("}")

        verification_snippet = "\n".join(lines)

    return (
        {
            "receipt": receipt_raw,
            "has_query": has_query,
            "is_valid_receipt": is_valid_receipt,
            "found": found,
            "election": election,
            "election_status": election_status,
            "submitted_date": submitted_date,
            "is_superseded": is_superseded,
            "is_final_ballot": is_final_ballot,
            "public_ballots_url": public_ballots_url,
            "rate_limited": False,
            "verification_snippet": verification_snippet,
        },
        200,
    )


@require_GET
def ballot_verify(request):
    return render(
        request,
        "core/ballot_verify.html",
        {
            "election_detail_url_template": reverse("election-detail", args=[123456789]).replace(
                "123456789", "__election_id__"
            ),
            "election_audit_log_url_template": reverse("election-audit-log", args=[123456789]).replace(
                "123456789", "__election_id__"
            ),
        },
    )


@require_GET
def ballot_verify_api(request):
    context, status_code = _ballot_verify_context(request)
    election = context["election"]
    return JsonResponse(
        {
            "receipt": context["receipt"],
            "has_query": context["has_query"],
            "is_valid_receipt": context["is_valid_receipt"],
            "found": context["found"],
            "election": (
                {
                    "id": election.id,
                    "name": election.name,
                }
                if election is not None
                else None
            ),
            "election_status": context["election_status"],
            "submitted_date": context["submitted_date"],
            "is_superseded": context["is_superseded"],
            "is_final_ballot": context["is_final_ballot"],
            "public_ballots_url": context["public_ballots_url"],
            "rate_limited": context["rate_limited"],
            "verification_snippet": context["verification_snippet"],
        },
        status=status_code,
    )
