"""Shared private helpers used across election view sub-modules."""

import datetime
from dataclasses import dataclass

from django.http import Http404
from django.urls import reverse

from core import elections_services
from core.elections_services import ElectionError
from core.email_context import user_email_context
from core.forms_elections import ElectionEndDateForm
from core.freeipa.user import FreeIPAUser
from core.models import Election
from core.views_utils import get_username


@dataclass(frozen=True)
class ElectionEndExtensionResult:
    success: bool
    errors: tuple[str, ...]


def _extend_election_end_from_post(*, request, election: Election) -> ElectionEndExtensionResult:
    if election.status != Election.Status.open:
        return ElectionEndExtensionResult(success=False, errors=("Only open elections can be extended.",))

    end_form = ElectionEndDateForm(request.POST, instance=election)
    if not end_form.is_valid():
        errors = tuple(str(msg) for msg in end_form.errors.get("end_datetime", []))
        if not errors:
            errors = ("Invalid end datetime.",)
        return ElectionEndExtensionResult(success=False, errors=errors)

    new_end = end_form.cleaned_data.get("end_datetime")
    if not isinstance(new_end, datetime.datetime):
        return ElectionEndExtensionResult(success=False, errors=("Invalid end datetime.",))

    try:
        elections_services.extend_election_end_datetime(
            election=election,
            new_end_datetime=new_end,
            actor=get_username(request) or None,
        )
    except ElectionError as exc:
        return ElectionEndExtensionResult(success=False, errors=(str(exc),))

    return ElectionEndExtensionResult(success=True, errors=())


def _get_active_election(election_id: int, *, fields: list[str] | None = None) -> Election:
    """Load an active election by PK or raise Http404."""
    qs = Election.objects.active().filter(pk=election_id)
    if fields:
        qs = qs.only(*fields)
    election = qs.first()
    if election is None:
        raise Http404
    return election


def _tally_elected_ids(election: Election) -> tuple[list[int], int]:
    """Extract elected candidate IDs and empty seat count from tally results.

    Returns (elected_ids, empty_seats). Empty seats is 0 unless the
    election is in tallied status.
    """
    tally_result = election.tally_result or {}
    elected_ids: list[int] = []
    for x in (tally_result.get("elected") or []):
        try:
            elected_ids.append(int(x))
        except (TypeError, ValueError):
            continue
    empty_seats = 0
    if election.status == Election.Status.tallied:
        empty_seats = election.number_of_seats - len(elected_ids)
    return elected_ids, empty_seats


def _elected_candidate_display(
    elected_ids: list[int],
    *,
    candidate_username_by_id: dict[int, str],
    users_by_username: dict[str, FreeIPAUser] | None = None,
) -> list[dict[str, str]]:
    """Build display dicts for elected candidates.

    Each dict contains username, profile_url, and full_name.
    Used by both the detail page and audit log to avoid duplicating
    the resolution logic.
    """
    result: list[dict[str, str]] = []
    for cid in elected_ids:
        username = candidate_username_by_id.get(cid, "")
        if not username:
            continue
        full_name = username
        if users_by_username is not None:
            user = users_by_username.get(username)
            full_name = user.full_name if user is not None else username
        result.append({
            "username": username,
            "full_name": full_name,
            "profile_url": reverse("user-profile", args=[username]),
        })
    return result


def _election_email_preview_context(
    *,
    request,
    election: Election,
) -> dict[str, object]:
    """Build the shared email preview context for election email templates.

    Produces the standard template variables (election fields, vote URLs,
    credential placeholder) plus user_email_context.  Callers merge
    additional keys (e.g. committee context) as needed.

    For saved elections, delegates the 7 core fields to
    ``_election_email_context`` in elections_services so they are defined
    in a single place.  For unsaved elections (no pk), falls back to
    null-safe inline construction.
    """
    username = get_username(request) or "preview"
    # Minimal Election for URL helpers so unsaved instances work.
    url_election = Election(id=int(election.id or 0))

    if election.pk is not None:
        # Saved election — delegate core fields to services helper.
        base = elections_services._election_email_context(election=election)
    else:
        # Unsaved election — null-safe inline construction.
        base = {
            "election_id": int(election.id or 0),
            "election_name": str(election.name or ""),
            "election_description": str(election.description or ""),
            "election_url": str(election.url or ""),
            "election_start_datetime": election.start_datetime or "",
            "election_end_datetime": election.end_datetime or "",
            "election_number_of_seats": election.number_of_seats or "",
        }

    return {
        **user_email_context(username=username),
        **base,
        "credential_public_id": "PREVIEW",
        "vote_url": elections_services.election_vote_url(
            request=request, election=url_election,
        ),
        "vote_url_with_credential_fragment": elections_services.election_vote_url_with_credential_fragment(
            request=request,
            election=url_election,
            credential_public_id="PREVIEW",
        ),
    }


def _load_candidate_users(usernames: set[str]) -> dict[str, FreeIPAUser]:
    """Load FreeIPA users for candidate/nominator usernames, with stable fallbacks.

    If FreeIPA doesn't return a user (e.g. account deleted), a minimal
    FreeIPAUser is constructed so rendering stays consistent.
    """
    result: dict[str, FreeIPAUser] = {}
    for username in sorted(usernames):
        user = FreeIPAUser.get(username)
        if user is None:
            user = FreeIPAUser(username, {"uid": [username], "memberof_group": []})
        result[username] = user
    return result
