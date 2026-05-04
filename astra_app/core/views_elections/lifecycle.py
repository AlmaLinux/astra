"""Election lifecycle actions: credential re-send, conclude, extend end date."""
import json
from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from core import elections_services
from core.elections_services import ElectionError
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.models import Election, VotingCredential
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required
from core.rate_limit import allow_request
from core.views_elections._helpers import _extend_election_end_from_post, _get_active_election
from core.views_send_mail import _CSV_SESSION_KEY
from core.views_utils import get_username, send_mail_url


@dataclass(frozen=True)
class ElectionCredentialResendResult:
    success: bool
    status_code: int
    message: str
    redirect_url: str | None = None
    recipient_count: int = 0


@dataclass(frozen=True)
class ElectionConcludeResult:
    ok: bool
    message: str
    tally_failed: bool = False


@dataclass(frozen=True)
class ElectionTallyResult:
    ok: bool
    message: str


def _send_mail_credentials_result(*, request: HttpRequest, election: Election, data: Mapping[str, object]) -> ElectionCredentialResendResult:
    if election.status != Election.Status.open:
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message="Only open elections can send credential reminders.",
        )

    admin_username = get_username(request)

    if not allow_request(
        scope="elections.credential_resend",
        key_parts=[str(election.id), admin_username],
        limit=settings.ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS,
    ):
        return ElectionCredentialResendResult(
            success=False,
            status_code=429,
            message="Too many resend attempts. Please try again later.",
        )

    target_username = str(data.get("username") or "").strip()

    credentials_qs = VotingCredential.objects.filter(election=election).exclude(freeipa_username__isnull=True)
    if not credentials_qs.exists():
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message=(
                "No voting credentials exist for this election. Credentials are issued only at election start (draft -> open)."
            ),
        )
    else:
        if target_username:
            credential_list = list(
                credentials_qs.filter(freeipa_username=target_username).only("freeipa_username", "public_id")
            )
        else:
            credential_list = list(credentials_qs.only("freeipa_username", "public_id"))

    if target_username and not credential_list:
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message="That user does not have a voting credential for this election.",
        )

    recipients: list[dict[str, str]] = []
    for credential in credential_list:
        username = str(credential.freeipa_username or "").strip()
        if not username:
            continue

        user = FreeIPAUser.get(username, respect_privacy=False)
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
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message="No credential recipients are available (missing email addresses?).",
        )

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

    redirect_url = send_mail_url(
        to_type="csv",
        to="",
        template_name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        extra_context={"election_committee_email": settings.ELECTION_COMMITTEE_EMAIL},
        reply_to=settings.ELECTION_COMMITTEE_EMAIL,
    )

    return ElectionCredentialResendResult(
        success=True,
        status_code=200,
        message="Credential resend prepared.",
        redirect_url=redirect_url,
        recipient_count=len(recipients),
    )


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_send_mail_credentials(request: HttpRequest, election_id: int) -> HttpResponse:
    election = _get_active_election(election_id)
    result = _send_mail_credentials_result(request=request, election=election, data=_request_data(request))

    if not result.success:
        if result.status_code == 429:
            return HttpResponse(result.message, status=429)
        messages.error(request, result.message)
        return redirect("election-detail", election_id=election.id)

    return redirect(result.redirect_url)


def _request_data(request: HttpRequest) -> Mapping[str, object]:
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            raw = json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if isinstance(raw, dict):
            return raw
        return {}
    return request.POST


def _confirm_election_action(*, data: Mapping[str, object], election: Election) -> bool:
    raw = str(data.get("confirm") or "").strip()
    if not raw:
        return False
    expected = str(election.name or "").strip()
    if not expected:
        return False
    return raw.casefold() == expected.casefold()


def _conclude_election(*, request: HttpRequest, election: Election, skip_tally: bool) -> ElectionConcludeResult:
    actor = get_username(request) or None

    try:
        elections_services.close_election(election=election, actor=actor)
    except ElectionError as exc:
        return ElectionConcludeResult(ok=False, message=str(exc))

    if skip_tally:
        return ElectionConcludeResult(ok=True, message="Election closed.")

    try:
        elections_services.tally_election(election=election, actor=actor)
    except ElectionError as exc:
        return ElectionConcludeResult(
            ok=True,
            message=f"Election closed, but tally failed: {exc}",
            tally_failed=True,
        )

    return ElectionConcludeResult(ok=True, message="Election closed and tallied.")


def _tally_election(*, request: HttpRequest, election: Election) -> ElectionTallyResult:
    actor = get_username(request) or None

    try:
        elections_services.tally_election(election=election, actor=actor)
    except ElectionError as exc:
        return ElectionTallyResult(ok=False, message=str(exc))

    return ElectionTallyResult(ok=True, message="Election tallied.")


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_conclude(request, election_id: int):
    election = _get_active_election(election_id)
    data = _request_data(request)

    if not _confirm_election_action(data=data, election=election):
        return HttpResponseBadRequest("Confirmation required.")

    skip_tally = bool(data.get("skip_tally"))

    result = _conclude_election(request=request, election=election, skip_tally=skip_tally)
    if result.ok:
        message_writer = messages.warning if result.tally_failed else messages.success
    else:
        message_writer = messages.error

    message_writer(request, result.message)
    return redirect("election-detail", election_id=election.id)


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_extend_end(request, election_id: int):
    election = _get_active_election(election_id)
    data = _request_data(request)

    if not _confirm_election_action(data=data, election=election):
        return HttpResponseBadRequest("Confirmation required.")

    result = _extend_election_end_from_post(request=request, election=election, data=data)
    if result.success:
        messages.success(request, "Election end date extended.")
    else:
        for msg in result.errors:
            messages.error(request, str(msg))

    return redirect("election-detail", election_id=election.id)


@require_POST
@json_permission_required(ASTRA_ADD_ELECTION)
def election_extend_end_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    data = _request_data(request)

    if not _confirm_election_action(data=data, election=election):
        return JsonResponse({"ok": False, "errors": ["Confirmation required."]}, status=400)

    result = _extend_election_end_from_post(request=request, election=election, data=data)
    payload: dict[str, object] = {"ok": result.success}
    status_code = 200
    if result.success:
        payload["election"] = {
            "id": election.id,
            "end_datetime": election.end_datetime.isoformat(),
        }
    else:
        payload["errors"] = list(result.errors)
        status_code = 400

    return JsonResponse(payload, status=status_code)


@require_POST
@json_permission_required(ASTRA_ADD_ELECTION)
def election_conclude_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    data = _request_data(request)

    if not _confirm_election_action(data=data, election=election):
        return JsonResponse({"ok": False, "errors": ["Confirmation required."]}, status=400)

    result = _conclude_election(
        request=request,
        election=election,
        skip_tally=bool(data.get("skip_tally")),
    )
    payload: dict[str, object] = {"ok": result.ok}
    status_code = 200
    if result.ok:
        election.refresh_from_db(fields=["status"])
        payload.update(
            {
                "message": result.message,
                "tally_failed": result.tally_failed,
                "election": {
                    "id": election.id,
                    "status": election.status,
                },
            }
        )
    else:
        payload["errors"] = [result.message]
        status_code = 400

    return JsonResponse(payload, status=status_code)


@require_POST
@json_permission_required(ASTRA_ADD_ELECTION)
def election_tally_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    data = _request_data(request)

    if not _confirm_election_action(data=data, election=election):
        return JsonResponse({"ok": False, "errors": ["Confirmation required."]}, status=400)

    result = _tally_election(request=request, election=election)
    if not result.ok:
        return JsonResponse({"ok": False, "errors": [result.message]}, status=400)

    election.refresh_from_db(fields=["status"])

    return JsonResponse(
        {
            "ok": True,
            "message": result.message,
            "election": {
                "id": election.id,
                "status": election.status,
            },
        }
    )


@require_POST
@json_permission_required(ASTRA_ADD_ELECTION)
def election_send_mail_credentials_api(request: HttpRequest, election_id: int) -> JsonResponse:
    election = _get_active_election(election_id)
    result = _send_mail_credentials_result(request=request, election=election, data=_request_data(request))

    payload: dict[str, object] = {"ok": result.success}
    if result.success:
        payload.update(
            {
                "redirect_url": result.redirect_url,
                "recipient_count": result.recipient_count,
            }
        )
    else:
        payload["errors"] = [result.message]

    return JsonResponse(payload, status=result.status_code if not result.success else 200)
