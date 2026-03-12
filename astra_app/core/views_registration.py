import datetime
import logging
from smtplib import SMTPRecipientsRefused
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from python_freeipa import ClientMeta, exceptions

from core.account_invitation_reconcile import (
    load_account_invitation_from_token,
    reconcile_account_invitation_for_username,
)
from core.email_context import system_email_context
from core.freeipa.circuit_breaker import _is_freeipa_availability_error
from core.freeipa.client import _build_freeipa_client, _with_freeipa_service_client_retry
from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.user import FreeIPAUser
from core.rate_limit import allow_request
from core.templated_email import queue_templated_email
from core.views_auth import (
    PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY,
    _emit_rate_limit_denial_log,
    _rate_limit_client_ip,
)
from core.views_utils import _normalize_str

from .forms_registration import PasswordSetForm, RegistrationForm, ResendRegistrationEmailForm
from .tokens import make_registration_activation_token, read_registration_activation_token

logger = logging.getLogger(__name__)

REGISTRATION_TEMPORARILY_UNAVAILABLE_MESSAGE = (
    "Registration is temporarily unavailable. Please try again in a few minutes. "
    "If the problem continues, contact support."
)
REGISTRATION_ACTIVATION_TEMPORARY_VERIFICATION_FAILURE_MESSAGE = (
    "We could not verify your registration right now. Please try the activation link again in a few minutes."
)
REGISTRATION_CONFIRM_TEMPORARY_VERIFICATION_FAILURE_MESSAGE = (
    "We could not verify your registration right now. Please try again in a few minutes or use the link from your email again."
)
REGISTRATION_ACTIVATION_FOLLOW_UP_WARNING_MESSAGE = (
    "Your account may already be ready. Please try signing in. If you cannot sign in yet, wait a few minutes and try again."
)


def _registration_freeipa_log_extra(
    request: HttpRequest,
    *,
    freeipa_phase: str,
    freeipa_exception_class: str,
    retry_attempted: bool,
    retry_outcome: str,
) -> dict[str, str | bool]:
    log_payload: dict[str, str | bool] = {
        "event": "astra.registration.freeipa_unauthorized",
        "component": "registration",
        "freeipa_phase": freeipa_phase,
        "freeipa_exception_class": freeipa_exception_class,
        "retry_attempted": retry_attempted,
        "retry_outcome": retry_outcome,
    }

    request_id = _normalize_str(request.META.get("HTTP_X_REQUEST_ID"))
    if not request_id:
        request_id = _normalize_str(request.headers.get("X-Request-ID"))
    if request_id:
        log_payload["request_id"] = request_id

    return log_payload


def _stageuser_add(client: ClientMeta, username: str, **kwargs: object) -> object:
    # python-freeipa call signatures vary by version. Try a couple.
    try:
        return client.stageuser_add(username, **kwargs)
    except TypeError:
        return client.stageuser_add(a_uid=username, **kwargs)


def _stageuser_show(client: ClientMeta, username: str) -> object:
    try:
        return client.stageuser_show(username)
    except TypeError:
        return client.stageuser_show(a_uid=username)


def _stageuser_activate(client: ClientMeta, username: str) -> object:
    try:
        return client.stageuser_activate(username)
    except TypeError:
        return client.stageuser_activate(a_uid=username)


def _load_registration_stage_data(
    username: str,
    *,
    include_client: bool = False,
) -> dict[str, object] | tuple[ClientMeta, dict[str, object] | None] | None:
    not_found = object()
    stage_client: ClientMeta | None = None

    def _get_service_client() -> ClientMeta:
        nonlocal stage_client

        stage_client = FreeIPAUser.get_client()
        return stage_client

    def _load_stageuser(client: ClientMeta) -> object:
        try:
            return _stageuser_show(client, username)
        except exceptions.NotFound:
            return not_found

    stage = _with_freeipa_service_client_retry(_get_service_client, _load_stageuser)
    if stage is not_found:
        stage_data = None
    elif not isinstance(stage, dict):
        stage_data = None
    else:
        result = stage.get("result")
        stage_data = result if isinstance(result, dict) else None

    if include_client:
        if stage_client is None:
            raise RuntimeError("Registration stage lookup did not return a client")
        return stage_client, stage_data

    return stage_data


def _send_registration_email(
    request: HttpRequest,
    *,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    invitation_token: str | None = None,
) -> None:
    payload = {"u": username, "e": email}
    normalized_invitation_token = _normalize_str(invitation_token)
    if normalized_invitation_token:
        payload["i"] = normalized_invitation_token
    token = make_registration_activation_token(payload)
    activate_url = request.build_absolute_uri(reverse("register-activate")) + f"?token={quote(token)}"
    confirm_url = request.build_absolute_uri(reverse("register-confirm")) + f"?username={quote(username)}"

    ttl_seconds = settings.EMAIL_VALIDATION_TOKEN_TTL_SECONDS
    ttl_minutes = max(1, int((ttl_seconds + 59) / 60))
    valid_until = timezone.now() + datetime.timedelta(seconds=ttl_seconds)
    # Include date + time to avoid ambiguity when expiry rolls into the next day.
    valid_until_utc = valid_until.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    full_name = f"{first_name} {last_name}".strip() or username

    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.REGISTRATION_EMAIL_TEMPLATE_NAME,
        context={
            **system_email_context(),
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "activate_url": activate_url,
            "confirm_url": confirm_url,
            "ttl_minutes": ttl_minutes,
            "valid_until_utc": valid_until_utc,
        },
    )


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST" and not settings.REGISTRATION_OPEN:
        messages.warning(request, "Registration is closed at the moment.")
        return redirect("login")

    invite_token = _normalize_str(request.GET.get("invite"))
    invitation_token = ""
    if invite_token:
        invitation = load_account_invitation_from_token(invite_token)
        if invitation is not None:
            request.session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = invite_token
            invitation_token = invite_token
    if not invitation_token:
        session_invite_token = _normalize_str(request.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY))
        if session_invite_token and load_account_invitation_from_token(session_invite_token) is not None:
            invitation_token = session_invite_token

    form = RegistrationForm(request.POST or None, initial={"invitation_token": invitation_token})

    if request.method == "POST":
        client_ip = _rate_limit_client_ip(request)
        submitted_username = _normalize_str(request.POST.get("username")).lower()
        submitted_email = _normalize_str(request.POST.get("email")).lower()

        limit = settings.AUTH_RATE_LIMIT_REGISTRATION_LIMIT
        window_seconds = settings.AUTH_RATE_LIMIT_REGISTRATION_WINDOW_SECONDS
        if not allow_request(
            scope="auth.registration_initiation",
            key_parts=[client_ip, submitted_username, submitted_email],
            limit=limit,
            window_seconds=window_seconds,
        ):
            form.add_error(None, "Too many registration attempts. Please try again later.")

            endpoint = request.resolver_match.view_name if request.resolver_match is not None else "register"
            subject = submitted_username or submitted_email
            _emit_rate_limit_denial_log(
                request,
                endpoint=endpoint,
                limit=limit,
                window_seconds=window_seconds,
                subject=subject,
            )

            return render(
                request,
                "core/register.html",
                {"form": form, "registration_open": settings.REGISTRATION_OPEN},
                status=429,
            )

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        first_name = form.cleaned_data["first_name"].strip()
        last_name = form.cleaned_data["last_name"].strip()
        email = form.cleaned_data["email"]
        invitation_token = _normalize_str(form.cleaned_data.get("invitation_token"))

        common_name = f"{first_name} {last_name}".strip() or username

        freeipa_phase = "service_login"
        last_unauthorized_phase: str | None = None
        get_client_calls = 0
        stageuser_add_calls = 0

        def _get_service_client() -> ClientMeta:
            nonlocal freeipa_phase, get_client_calls, last_unauthorized_phase

            freeipa_phase = "service_login"
            get_client_calls += 1
            try:
                return FreeIPAUser.get_client()
            except exceptions.Unauthorized:
                last_unauthorized_phase = freeipa_phase
                raise

        def _create_stageuser(client: ClientMeta) -> object:
            nonlocal freeipa_phase, stageuser_add_calls, last_unauthorized_phase

            freeipa_phase = "stageuser_add"
            stageuser_add_calls += 1
            try:
                return _stageuser_add(
                    client,
                    username,
                    o_givenname=first_name,
                    o_sn=last_name,
                    o_cn=common_name,
                    o_mail=email,
                    o_loginshell="/bin/bash",
                    fasstatusnote="active",
                )
            except exceptions.Unauthorized:
                last_unauthorized_phase = freeipa_phase
                raise

        try:
            result = _with_freeipa_service_client_retry(_get_service_client, _create_stageuser)
            _ = result
        except exceptions.DuplicateEntry:
            form.add_error(None, f"The username '{username}' or the email address '{email}' are already taken.")
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except exceptions.ValidationError as e:
            # FreeIPA often encodes field name inside the message; keep it generic.
            logger.info("Registration validation error username=%s error=%s", username, e)
            form.add_error(None, str(e))
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except exceptions.Unauthorized as e:
            retry_attempted = get_client_calls > 1 or stageuser_add_calls > 1
            logged_freeipa_phase = last_unauthorized_phase or freeipa_phase
            logger.warning(
                "Registration FreeIPA Unauthorized",
                extra=_registration_freeipa_log_extra(
                    request,
                    freeipa_phase=logged_freeipa_phase,
                    freeipa_exception_class=e.__class__.__name__,
                    retry_attempted=retry_attempted,
                    retry_outcome="failed" if retry_attempted else "not_attempted",
                ),
            )
            form.add_error(None, REGISTRATION_TEMPORARILY_UNAVAILABLE_MESSAGE)
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except exceptions.FreeIPAError as e:
            logger.warning("Registration FreeIPA error username=%s error=%s", username, e)
            if settings.DEBUG:
                form.add_error(None, f"An error occurred while creating the account (debug): {e}")
            else:
                form.add_error(None, "An error occurred while creating the account, please try again.")
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except Exception as e:
            logger.exception("Registration unexpected error username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Unable to create account (debug): {e}")
            else:
                form.add_error(None, "Unable to create account due to an internal error.")
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})

        retry_attempted = get_client_calls > 1 or stageuser_add_calls > 1
        if retry_attempted:
            logged_freeipa_phase = last_unauthorized_phase or freeipa_phase
            logger.info(
                "Registration FreeIPA Unauthorized recovered",
                extra=_registration_freeipa_log_extra(
                    request,
                    freeipa_phase=logged_freeipa_phase,
                    freeipa_exception_class="Unauthorized",
                    retry_attempted=True,
                    retry_outcome="recovered",
                ),
            )

        try:
            _send_registration_email(
                request,
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                invitation_token=invitation_token,
            )
        except (ConnectionRefusedError, SMTPRecipientsRefused) as e:
            logger.error("Registration email send failed username=%s email=%s error=%s", username, email, e)
            messages.error(request, "We could not send you the address validation email, please retry later")
        except Exception as e:
            logger.exception("Registration email send unexpected failure username=%s email=%s", username, email)
            if settings.DEBUG:
                messages.error(request, f"We could not send the validation email (debug): {e}")
            else:
                messages.error(request, "We could not send you the address validation email, please retry later")

        return redirect(f"{reverse('register-confirm')}?username={username}")

    return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})


def confirm(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    username = _normalize_str(request.GET.get("username"))
    if not username:
        return HttpResponse("No username provided", status=400)

    try:
        stage_data = _load_registration_stage_data(username)
    except Exception as exc:
        if isinstance(exc, FreeIPAUnavailableError) or _is_freeipa_availability_error(exc):
            raise

        logger.exception(
            "Registration confirm verification failed username=%s error_class=%s error=%s",
            username,
            exc.__class__.__name__,
            exc,
        )
        messages.error(request, REGISTRATION_CONFIRM_TEMPORARY_VERIFICATION_FAILURE_MESSAGE)
        return redirect("register")

    email = None
    first_name = None
    last_name = None
    if isinstance(stage_data, dict):
        email = (stage_data.get("mail") or [None])[0] if isinstance(stage_data.get("mail"), list) else stage_data.get("mail")
        first_name = (stage_data.get("givenname") or [None])[0] if isinstance(stage_data.get("givenname"), list) else stage_data.get("givenname")
        last_name = (stage_data.get("sn") or [None])[0] if isinstance(stage_data.get("sn"), list) else stage_data.get("sn")

    form = ResendRegistrationEmailForm(request.POST or None, initial={"username": username})
    if request.method == "POST" and form.is_valid():
        invitation_token = None
        pending_invitation_token = _normalize_str(request.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY))
        if pending_invitation_token and load_account_invitation_from_token(pending_invitation_token) is not None:
            invitation_token = pending_invitation_token
        try:
            _send_registration_email(
                request,
                username=username,
                email=(email or ""),
                first_name=(first_name or ""),
                last_name=(last_name or ""),
                invitation_token=invitation_token,
            )
        except Exception:
            logger.exception("Resend registration email failed username=%s", username)
            messages.error(request, "We could not send you the address validation email, please retry later")
        else:
            messages.success(
                request,
                "The address validation email has be sent again. Make sure it did not land in your spam folder",
            )
        return redirect(request.get_full_path())

    return render(
        request,
        "core/register_confirm.html",
        {
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "form": form,
        },
    )


def activate(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    token_string = _normalize_str(request.GET.get("token"))
    if not token_string:
        messages.warning(request, "No token provided, please check your email validation link.")
        return redirect("register")

    try:
        token = read_registration_activation_token(token_string)
    except signing.SignatureExpired:
        messages.warning(request, "This token is no longer valid, please register again.")
        return redirect("register")
    except signing.BadSignature:
        messages.warning(request, "The token is invalid, please register again.")
        return redirect("register")

    username = _normalize_str(token.get("u"))
    token_email = _normalize_str(token.get("e")).lower()
    invitation_token = _normalize_str(token.get("i"))

    try:
        client, stage_data = _load_registration_stage_data(username, include_client=True)
    except Exception as exc:
        if isinstance(exc, FreeIPAUnavailableError) or _is_freeipa_availability_error(exc):
            raise

        logger.exception(
            "Registration activation verification failed username=%s error_class=%s error=%s",
            username,
            exc.__class__.__name__,
            exc,
        )
        messages.warning(request, REGISTRATION_ACTIVATION_TEMPORARY_VERIFICATION_FAILURE_MESSAGE)
        return redirect("register")

    if stage_data is None:
        messages.warning(request, "This user cannot be found, please register again.")
        return redirect("register")

    user_email = None
    if isinstance(stage_data, dict):
        raw = stage_data.get("mail")
        if isinstance(raw, list):
            user_email = (raw[0] if raw else None)
        else:
            user_email = raw
    if _normalize_str(user_email).lower() != token_email:
        logger.error(
            "Registration token email mismatch username=%s token_email=%s user_email=%s",
            username,
            token_email,
            user_email,
        )
        messages.warning(request, "The username and the email address don't match the token you used, please register again.")
        return redirect("register")

    form = PasswordSetForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["password"]
        try:
            _stageuser_activate(client, username)

            # Set password as a privileged client.
            try:
                client.user_mod(username, o_userpassword=password)
            except TypeError:
                client.user_mod(a_uid=username, o_userpassword=password)

            # Try to un-expire it by changing it "as the user".
            try:
                c = _build_freeipa_client()
                # python-freeipa signature: change_password(username, new_password, old_password, otp=None)
                c.change_password(username, password, password, otp=None)
            except exceptions.PWChangePolicyError as e:
                logger.info("Activation succeeded but password policy rejected username=%s error=%s", username, e)
                messages.warning(
                    request,
                    "Your account has been created, but the password you chose does not comply with policy and has been set as expired. Please log in and change it.",
                )
                return redirect("login")
            except Exception as e:
                logger.warning("Activation password unexpire step failed username=%s error=%s", username, e)
                messages.warning(
                    request,
                    "Your account has been created, but an error occurred while setting your password. You may need to change it after logging in.",
                )
                return redirect("login")

        except exceptions.FreeIPAError as e:
            logger.error("Activation failed username=%s error=%s", username, e)
            form.add_error(None, "Something went wrong while creating your account, please try again later.")
        except Exception as e:
            logger.exception("Activation failed (unexpected) username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Something went wrong (debug): {e}")
            else:
                form.add_error(None, "Something went wrong while creating your account, please try again later.")
        else:
            follow_up_warning: str | None = None
            clear_pending_invitation_token = True
            if invitation_token:
                try:
                    invitation = load_account_invitation_from_token(invitation_token)
                    if invitation is not None:
                        reconcile_account_invitation_for_username(
                            invitation=invitation,
                            username=username,
                            now=timezone.now(),
                        )
                except Exception as exc:
                    logger.exception(
                        "Registration activation post-success follow-up failed username=%s error_class=%s error=%s",
                        username,
                        exc.__class__.__name__,
                        exc,
                    )
                    follow_up_warning = REGISTRATION_ACTIVATION_FOLLOW_UP_WARNING_MESSAGE
                    clear_pending_invitation_token = False

            if clear_pending_invitation_token:
                request.session.pop(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, None)

            messages.success(request, "Congratulations, your account has been created! Go ahead and sign in to proceed.")
            if follow_up_warning is not None:
                messages.warning(request, follow_up_warning)
            return redirect("login")

    return render(request, "core/register_activate.html", {"form": form, "username": username})
