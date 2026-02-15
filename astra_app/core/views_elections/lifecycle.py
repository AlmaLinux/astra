"""Election lifecycle actions: credential re-send, conclude, extend end date."""
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from core import elections_services
from core.backends import FreeIPAUser
from core.elections_services import ElectionError, issue_voting_credentials_from_memberships
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.models import Election, VotingCredential
from core.permissions import ASTRA_ADD_ELECTION
from core.rate_limit import allow_request
from core.views_elections._helpers import _extend_election_end_from_post, _get_active_election
from core.views_send_mail import _CSV_SESSION_KEY
from core.views_utils import get_username, send_mail_url


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_send_mail_credentials(request: HttpRequest, election_id: int) -> HttpResponse:
    election = _get_active_election(election_id)

    if election.status != Election.Status.open:
        messages.error(request, "Only open elections can send credential reminders.")
        return redirect("election-detail", election_id=election.id)

    admin_username = get_username(request)

    if not allow_request(
        scope="elections.credential_resend",
        key_parts=[str(election.id), admin_username],
        limit=settings.ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS,
    ):
        return HttpResponse("Too many resend attempts. Please try again later.", status=429)

    target_username = str(request.POST.get("username") or "").strip()

    credentials_qs = VotingCredential.objects.filter(election=election).exclude(freeipa_username__isnull=True)
    if not credentials_qs.exists():
        issued = issue_voting_credentials_from_memberships(election=election)
        by_username = {c.freeipa_username: c for c in issued if c.freeipa_username}
        if target_username:
            credential_list = [by_username[target_username]] if target_username in by_username else []
        else:
            credential_list = list(by_username.values())
    else:
        if target_username:
            credential_list = list(
                credentials_qs.filter(freeipa_username=target_username).only("freeipa_username", "public_id")
            )
        else:
            credential_list = list(credentials_qs.only("freeipa_username", "public_id"))

    if target_username and not credential_list:
        messages.error(request, "That user does not have a voting credential for this election.")
        return redirect("election-detail", election_id=election.id)

    recipients: list[dict[str, str]] = []
    for credential in credential_list:
        username = str(credential.freeipa_username or "").strip()
        if not username:
            continue

        user = FreeIPAUser.get(username)
        if user is None or not user.email:
            continue

        tz_name = _get_freeipa_timezone_name(user)

        ctx = elections_services.build_voting_credential_email_context(
            request=request,
            election=election,
            username=username,
            credential_public_id=str(credential.public_id),
            tz_name=tz_name,
            user=user,
        )
        recipients.append({str(k): str(v) for k, v in ctx.items()})

    if not recipients:
        messages.error(request, "No credential recipients are available (missing email addresses?).")
        return redirect("election-detail", election_id=election.id)

    request.session[_CSV_SESSION_KEY] = json.dumps(
        {
            "header_to_var": {
                "Email": "email",
                "Username": "username",
                "First name": "first_name",
                "Last name": "last_name",
                "Full name": "full_name",
                "Election ID": "election_id",
                "Election name": "election_name",
                "Election description": "election_description",
                "Election URL": "election_url",
                "Election start": "election_start_datetime",
                "Election end": "election_end_datetime",
                "Number of seats": "election_number_of_seats",
                "Credential": "credential_public_id",
                "Vote URL": "vote_url",
                "Vote URL (with credential)": "vote_url_with_credential_fragment",
            },
            "recipients": recipients,
        }
    )

    return redirect(
        send_mail_url(
            to_type="csv",
            to="",
            template_name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
            extra_context={"election_committee_email": settings.ELECTION_COMMITTEE_EMAIL},
            reply_to=settings.ELECTION_COMMITTEE_EMAIL,
        )
    )


def _confirm_election_action(*, request: HttpRequest, election: Election) -> bool:
    raw = str(request.POST.get("confirm") or "").strip()
    if not raw:
        return False
    expected = str(election.name or "").strip()
    if not expected:
        return False
    return raw.casefold() == expected.casefold()


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_conclude(request, election_id: int):
    election = _get_active_election(election_id)

    if not _confirm_election_action(request=request, election=election):
        return HttpResponseBadRequest("Confirmation required.")

    skip_tally = bool(request.POST.get("skip_tally"))

    actor = get_username(request) or None

    try:
        elections_services.close_election(election=election, actor=actor)
    except ElectionError as exc:
        messages.error(request, str(exc))
        return redirect("election-detail", election_id=election.id)

    if skip_tally:
        messages.success(request, "Election closed.")
        return redirect("election-detail", election_id=election.id)

    try:
        elections_services.tally_election(election=election, actor=actor)
    except ElectionError as exc:
        messages.error(request, f"Election closed, but tally failed: {exc}")
        return redirect("election-detail", election_id=election.id)

    messages.success(request, "Election closed and tallied.")
    return redirect("election-detail", election_id=election.id)


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_extend_end(request, election_id: int):
    election = _get_active_election(election_id)

    if not _confirm_election_action(request=request, election=election):
        return HttpResponseBadRequest("Confirmation required.")

    result = _extend_election_end_from_post(request=request, election=election)
    if not result.success:
        for msg in result.errors:
            messages.error(request, str(msg))
        return redirect("election-detail", election_id=election.id)

    messages.success(request, "Election end date extended.")
    return redirect("election-detail", election_id=election.id)
