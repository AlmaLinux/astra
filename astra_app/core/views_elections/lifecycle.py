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
from django.views.decorators.http import require_GET, require_POST
from post_office.models import EmailTemplate

from core import elections_services
from core.elections_services import ElectionError
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.models import Election, ElectionRoll, VotingCredential
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required
from core.rate_limit import allow_request
from core.views_elections._helpers import (
    CREDENTIAL_EMAIL_SECRET_VARIABLES,
    _election_email_preview_context,
    _extend_election_end_from_post,
    _get_active_election,
)
from core.views_utils import get_username


@dataclass(frozen=True)
class ElectionCredentialResendResult:
    success: bool
    status_code: int
    message: str
    redirect_url: str | None = None
    recipient_count: int = 0
    success_message: str | None = None
    error_message: str | None = None


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
    _SENDABLE_STATUSES = {Election.Status.open, Election.Status.closed, Election.Status.tallied}
    if election.status not in _SENDABLE_STATUSES:
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message="Emails can only be sent for open, closed, or tallied elections.",
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

    # Optional custom template content from the compose modal.
    subject_template = str(data.get("subject_template") or "").strip() or None
    html_template = str(data.get("html_template") or "").strip() or None
    text_template = str(data.get("text_template") or "").strip() or None

    include_credentials = election.status == Election.Status.open

    # For open elections, derive recipients from credentials (which retain
    # freeipa_username).  For closed/tallied elections, credentials are
    # anonymized so we fall back to the eligible-voters group membership.
    if include_credentials:
        credentials_qs = VotingCredential.objects.filter(election=election).exclude(freeipa_username__isnull=True)
        if not credentials_qs.exists():
            return ElectionCredentialResendResult(
                success=False,
                status_code=400,
                message=(
                    "No voting credentials exist for this election. "
                    "Credentials are issued only at election start (draft -> open)."
                ),
            )
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
    else:
        # Closed/tallied: credentials are anonymized, so use the permanent
        # ElectionRoll snapshot that was captured at credential issuance.
        roll_qs = ElectionRoll.objects.filter(election=election)

        if target_username:
            if not roll_qs.filter(freeipa_username=target_username).exists():
                return ElectionCredentialResendResult(
                    success=False,
                    status_code=400,
                    message="That user is not an eligible voter for this election.",
                )
            eligible_usernames = [target_username]
        else:
            eligible_usernames = list(roll_qs.values_list("freeipa_username", flat=True))

        if not eligible_usernames:
            return ElectionCredentialResendResult(
                success=False,
                status_code=400,
                message="No eligible voters found for this election.",
            )

        # Build a lightweight stub list so the delivery loop below works
        # uniformly.  public_id is unused when include_credentials is False.
        credential_list = [
            type("_RollStub", (), {"freeipa_username": username, "public_id": ""})()
            for username in eligible_usernames
        ]

    deliveries: list[tuple[str, str, str, str | None]] = []
    for credential in credential_list:
        username = str(credential.freeipa_username or "").strip()
        if not username:
            continue

        user = FreeIPAUser.get(username, respect_privacy=False)
        if user is None or not user.email:
            continue

        tz_name = _get_freeipa_timezone_name(user)

        deliveries.append(
            (
                username,
                user.email,
                str(credential.public_id),
                tz_name,
            )
        )

    if not deliveries:
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message="No credential recipients are available (missing email addresses?).",
        )

    recipient_count = 0
    failure_count = 0
    for username, email, credential_public_id, tz_name in deliveries:
        try:
            elections_services.send_voting_credential_email(
                request=request,
                election=election,
                username=username,
                email=email,
                credential_public_id=credential_public_id if include_credentials else "",
                tz_name=tz_name,
                subject_template=subject_template,
                html_template=html_template,
                text_template=text_template,
                include_credentials=include_credentials,
            )
            recipient_count += 1
        except Exception:
            failure_count += 1

    if recipient_count == 0 and failure_count > 0:
        failure_label = "email" if failure_count == 1 else "emails"
        failure_message = f"Failed to queue {failure_count} {failure_label}."
        return ElectionCredentialResendResult(
            success=False,
            status_code=400,
            message=failure_message,
            error_message=failure_message,
        )

    recipient_label = "recipient" if recipient_count == 1 else "recipients"
    email_kind = "voting credential email" if include_credentials else "email"
    success_message = f"Queued {email_kind} for {recipient_count} {recipient_label}."

    if failure_count > 0:
        failure_label = "email" if failure_count == 1 else "emails"
        failure_message = f"Failed to queue {failure_count} {failure_label}."
        return ElectionCredentialResendResult(
            success=True,
            status_code=200,
            message=f"{success_message} {failure_message}",
            recipient_count=recipient_count,
            success_message=success_message,
            error_message=failure_message,
        )

    return ElectionCredentialResendResult(
        success=True,
        status_code=200,
        message=success_message,
        recipient_count=recipient_count,
        success_message=success_message,
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

    if result.success_message:
        messages.success(request, result.success_message)
    if result.error_message:
        messages.error(request, result.error_message)
    return redirect("election-detail", election_id=election.id)


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
                "message": result.message,
                "recipient_count": result.recipient_count,
            }
        )
        if result.error_message:
            payload["errors"] = [result.error_message]
    else:
        payload["errors"] = [result.message]

    return JsonResponse(payload, status=result.status_code if not result.success else 200)


def _resolve_election_email_template(election: Election) -> tuple[str, str, str]:
    """Return (subject, html, text) for the election's voting credential email.

    Uses the election's snapshot fields when populated, otherwise falls back to
    the linked EmailTemplate FK or the default named template.
    """
    if (
        election.voting_email_subject.strip()
        or election.voting_email_html.strip()
        or election.voting_email_text.strip()
    ):
        return (
            election.voting_email_subject,
            election.voting_email_html,
            election.voting_email_text,
        )

    template: EmailTemplate | None = None
    if election.voting_email_template_id is not None:
        template = election.voting_email_template
    else:
        template = EmailTemplate.objects.filter(
            name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        ).first()

    if template is not None:
        return (
            template.subject or "",
            template.html_content or "",
            template.content or "",
        )

    return ("", "", "")


def _credential_email_variable_examples(
    request: HttpRequest,
    election: Election,
    *,
    preview_username: str | None = None,
) -> list[dict[str, str]]:
    """Build variable examples pre-filled from the real election data.

    Derives the variable list from ``_election_email_preview_context``,
    excluding the secret per-recipient keys.
    """
    ctx = _election_email_preview_context(
        request=request, election=election, preview_username=preview_username,
    )

    return [
        {"name": name, "example": str(value)}
        for name, value in ctx.items()
        if name not in CREDENTIAL_EMAIL_SECRET_VARIABLES
    ]


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def election_credential_email_template_api(request: HttpRequest, election_id: int) -> JsonResponse:
    """Return the election's credential email template content and available variables."""
    election = _get_active_election(election_id)

    preview_username = str(request.GET.get("preview_username") or "").strip() or None

    subject, html_content, text_content = _resolve_election_email_template(election)

    # Template selector options.
    templates = list(EmailTemplate.objects.all().order_by("name"))
    template_options = [
        {"id": t.pk, "name": t.name}
        for t in templates
    ]

    # Determine selected template: FK on election, or default.
    selected_template_id: int | None = None
    if election.voting_email_template_id is not None:
        selected_template_id = election.voting_email_template_id
    else:
        default = EmailTemplate.objects.filter(
            name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        ).only("pk").first()
        if default is not None:
            selected_template_id = default.pk

    return JsonResponse({
        "subject": subject,
        "html_content": html_content,
        "text_content": text_content,
        "variables": _credential_email_variable_examples(
            request, election, preview_username=preview_username,
        ),
        "template_options": template_options,
        "selected_template_id": selected_template_id,
    })
