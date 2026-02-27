import hashlib
import hmac
import logging
import secrets
from typing import override

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core import signing
from django.forms.forms import NON_FIELD_ERRORS
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from python_freeipa import exceptions

from core.account_invitation_reconcile import (
    load_account_invitation_from_token,
    reconcile_account_invitation_for_username,
)
from core.freeipa.client import _build_freeipa_client
from core.freeipa.user import FreeIPAUser
from core.rate_limit import allow_request
from core.tokens import make_signed_token
from core.views_utils import _normalize_str, get_username

from .forms_auth import (
    ExpiredPasswordChangeForm,
    FreeIPAAuthenticationForm,
    PasswordResetRequestForm,
    PasswordResetSetForm,
    SyncTokenForm,
)
from .password_reset import (
    PASSWORD_RESET_TOKEN_PURPOSE,
    find_user_for_password_reset,
    read_password_reset_token,
    send_password_reset_email,
    send_password_reset_success_email,
)

logger = logging.getLogger(__name__)
PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY = "pending_account_invitation_token"


def _rate_limit_client_ip(request: HttpRequest) -> str:
    return _normalize_str(request.META.get("REMOTE_ADDR"))


def _emit_rate_limit_denial_log(
    request: HttpRequest,
    *,
    endpoint: str,
    limit: int,
    window_seconds: int,
    subject: str,
) -> None:
    client_ip = _rate_limit_client_ip(request)

    log_payload: dict[str, str | int | bool] = {
        "event": "astra.security.rate_limit.denied",
        "component": "auth",
        "outcome": "denied",
        "endpoint": endpoint,
        "http_method": "POST",
        "limit": limit,
        "window_seconds": window_seconds,
    }

    request_id = _normalize_str(request.META.get("HTTP_X_REQUEST_ID"))
    if not request_id:
        request_id = _normalize_str(request.headers.get("X-Request-ID"))
    if request_id:
        log_payload["request_id"] = request_id

    secret = str(settings.SECRET_KEY).encode("utf-8")
    if client_ip:
        log_payload["ip_hash"] = hmac.new(
            key=secret,
            msg=client_ip.lower().encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
    else:
        log_payload["ip_present"] = False

    if subject:
        log_payload["subject_hash"] = hmac.new(
            key=secret,
            msg=subject.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

    logger.warning("Rate limit denied", extra=log_payload)


class FreeIPALoginView(auth_views.LoginView):
    """LoginView that can redirect / message based on FreeIPA backend signals."""

    template_name = "core/login.html"
    authentication_form = FreeIPAAuthenticationForm

    @override
    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect("home")

        invite_token = ""
        if request.method == "GET":
            invite_token = _normalize_str(request.GET.get("invite"))
        if invite_token and load_account_invitation_from_token(invite_token) is not None:
            request.session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = invite_token
        return super().dispatch(request, *args, **kwargs)

    @override
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        client_ip = _rate_limit_client_ip(request)
        submitted_username = _normalize_str(request.POST.get("username")).lower()

        limit = settings.AUTH_RATE_LIMIT_LOGIN_LIMIT
        window_seconds = settings.AUTH_RATE_LIMIT_LOGIN_WINDOW_SECONDS
        if not allow_request(
            scope="auth.login",
            key_parts=[client_ip, submitted_username],
            limit=limit,
            window_seconds=window_seconds,
        ):
            form = self.get_form()
            form.add_error(None, "Too many login attempts. Please try again later.")

            endpoint = request.resolver_match.view_name if request.resolver_match is not None else "login"
            _emit_rate_limit_denial_log(
                request,
                endpoint=endpoint,
                limit=limit,
                window_seconds=window_seconds,
                subject=submitted_username,
            )

            response = self.form_invalid(form)
            response.status_code = 429
            return response

        return super().post(request, *args, **kwargs)

    @override
    def get_success_url(self) -> str:
        username = get_username(self.request)
        if username:
            return reverse("user-profile", kwargs={"username": username})

        return super().get_success_url()

    def form_invalid(self, form) -> HttpResponse:
        request: HttpRequest = self.request

        if getattr(request, "_freeipa_password_expired", False):
            return redirect("password-expired")

        msg = getattr(request, "_freeipa_auth_error", None)
        if msg:
            form.errors.pop(NON_FIELD_ERRORS, None)
            form.add_error(None, msg)

        return super().form_invalid(form)

    def form_valid(self, form) -> HttpResponse:
        response = super().form_valid(form)

        invite_token = _normalize_str(
            self.request.POST.get("invite")
            or self.request.GET.get("invite")
            or self.request.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY)
        )
        if not invite_token:
            return response

        invitation = load_account_invitation_from_token(invite_token)
        if invitation is None:
            return response

        username = get_username(self.request)
        if not username:
            return response

        reconcile_account_invitation_for_username(
            invitation=invitation,
            username=username,
            now=timezone.now(),
        )
        self.request.session.pop(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, None)

        return response


def password_reset_request(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST":
        client_ip = _rate_limit_client_ip(request)
        submitted_identifier = _normalize_str(request.POST.get("username_or_email")).lower()

        limit = settings.AUTH_RATE_LIMIT_PASSWORD_RESET_LIMIT
        window_seconds = settings.AUTH_RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS
        if not allow_request(
            scope="auth.password_reset_request",
            key_parts=[client_ip, submitted_identifier],
            limit=limit,
            window_seconds=window_seconds,
        ):
            form.add_error(None, "Too many password reset attempts. Please try again later.")

            endpoint = request.resolver_match.view_name if request.resolver_match is not None else "password-reset"
            _emit_rate_limit_denial_log(
                request,
                endpoint=endpoint,
                limit=limit,
                window_seconds=window_seconds,
                subject=submitted_identifier,
            )

            return render(request, "core/password_reset_request.html", {"form": form}, status=429)

    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["username_or_email"]
        user = find_user_for_password_reset(identifier)
        if user is not None:
            username = _normalize_str(user.username)
            email = _normalize_str(user.email)
            last_password_change = _normalize_str(user.last_password_change)
            pending_invitation_token = _normalize_str(
                request.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY)
            )
            if pending_invitation_token and load_account_invitation_from_token(pending_invitation_token) is None:
                pending_invitation_token = ""
            if username and email:
                try:
                    send_password_reset_email(
                        request=request,
                        username=username,
                        email=email,
                        last_password_change=last_password_change,
                        invitation_token=pending_invitation_token or None,
                    )
                    if pending_invitation_token:
                        request.session.pop(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, None)
                except Exception:
                    logger.exception("Password reset email send failed username=%s", username)

        messages.success(
            request,
            "If an account exists for that username/email, a password reset email has been sent.",
        )
        return redirect("login")

    return render(request, "core/password_reset_request.html", {"form": form})


def password_reset_confirm(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    token_string = _normalize_str(request.POST.get("token") or request.GET.get("token"))
    if not token_string:
        messages.warning(request, "No token provided.")
        return redirect("login")

    try:
        token = read_password_reset_token(token_string)
    except signing.SignatureExpired:
        messages.warning(request, "This password reset link has expired. Please request a new one.")
        return redirect("password-reset")
    except signing.BadSignature:
        messages.warning(request, "This password reset link is invalid. Please request a new one.")
        return redirect("password-reset")

    username = _normalize_str(token.get("u"))
    token_email = _normalize_str(token.get("e")).lower()
    token_lpc = _normalize_str(token.get("lpc"))
    if not username:
        messages.warning(request, "This password reset link is invalid. Please request a new one.")
        return redirect("password-reset")

    user = find_user_for_password_reset(username)
    if user is None:
        messages.warning(request, "This password reset link is invalid. Please request a new one.")
        return redirect("password-reset")

    user_email = _normalize_str(user.email).lower()
    if token_email and user_email and token_email != user_email:
        messages.warning(request, "This password reset link is no longer valid. Please request a new one.")
        return redirect("password-reset")

    user_lpc = _normalize_str(user.last_password_change)
    if token_lpc != user_lpc:
        messages.warning(
            request,
            "Your password has changed since you requested this link. Please request a new password reset email.",
        )
        return redirect("password-reset")

    def _user_has_otp_tokens() -> bool:
        try:
            svc = FreeIPAUser.get_client()
            res = svc.otptoken_find(o_ipatokenowner=username, o_all=True)
            tokens = res.get("result", []) if isinstance(res, dict) else []
            return bool(tokens)
        except exceptions.NotFound:
            # User might not exist (or be visible) in some environments; treat
            # as no OTP so the reset UI remains usable.
            return False
        except AttributeError:
            # Some client versions/environments may not expose the OTP API.
            return False
        except Exception:
            logger.debug("Password reset: OTP token lookup failed username=%s", username, exc_info=True)
            return False

    has_otp = _user_has_otp_tokens()

    form = PasswordResetSetForm(request.POST or None, require_otp=has_otp)
    if request.method == "POST" and form.is_valid():
        new_password = form.cleaned_data["password"]
        otp = _normalize_str(form.cleaned_data.get("otp")) or None

        if has_otp and not otp:
            form.add_error("otp", "One-Time Password is required for this account.")
            return render(
                request,
                "core/password_reset_confirm.html",
                {"form": form, "username": username, "token": token_string},
            )

        # Noggin-style safety: set a random temporary password first, then use the
        # user's password-change endpoint with OTP. This avoids leaving the user's
        # chosen password set if OTP validation fails.
        temp_password = secrets.token_urlsafe(32)

        try:
            svc = FreeIPAUser.get_client()
            try:
                svc.user_mod(username, o_userpassword=temp_password)
            except TypeError:
                svc.user_mod(a_uid=username, o_userpassword=temp_password)

            client = _build_freeipa_client()
            # python-freeipa signature: change_password(username, new_password, old_password, otp=None)
            client.change_password(username, new_password, temp_password, otp=otp)
        except exceptions.PWChangePolicyError:
            form.add_error(None, "Password change rejected by policy. Please choose a stronger password.")
        except exceptions.PWChangeInvalidPassword:
            # Most commonly: wrong/missing OTP for OTP-enabled accounts.
            # Changing the temp password updates the user's password-change timestamp,
            # which invalidates the token; regenerate so the user can retry.
            refreshed = find_user_for_password_reset(username)
            refreshed_lpc = _normalize_str(refreshed.last_password_change) if refreshed else ""
            next_token_payload = {
                "p": PASSWORD_RESET_TOKEN_PURPOSE,
                "u": username,
                "e": user_email,
                "lpc": refreshed_lpc,
            }
            invitation_token = _normalize_str(token.get("i"))
            if invitation_token:
                next_token_payload["i"] = invitation_token

            next_token = make_signed_token(next_token_payload)
            form.add_error("otp" if has_otp else None, "Incorrect value.")
            return render(
                request,
                "core/password_reset_confirm.html",
                {"form": form, "username": username, "token": next_token},
            )
        except exceptions.FreeIPAError:
            logger.exception("Password reset failed username=%s", username)
            form.add_error(None, "Unable to reset password due to a FreeIPA error.")
        except Exception as e:
            logger.exception("Password reset failed (unexpected) username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Unable to reset password (debug): {e}")
            else:
                form.add_error(None, "Unable to reset password due to an internal error.")
        else:
            invitation_token = _normalize_str(token.get("i"))
            if invitation_token:
                invitation = load_account_invitation_from_token(invitation_token)
                if invitation is not None:
                    reconcile_account_invitation_for_username(
                        invitation=invitation,
                        username=username,
                        now=timezone.now(),
                    )

            request.session.pop(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, None)

            try:
                send_password_reset_success_email(request=request, username=username, email=user_email)
            except Exception:
                logger.exception("Password reset success email send failed username=%s", username)
            messages.success(request, "Password updated. Please log in.")
            return redirect("login")

    return render(
        request,
        "core/password_reset_confirm.html",
        {"form": form, "username": username, "token": token_string},
    )


def password_expired(request: HttpRequest) -> HttpResponse:
    """Password-expired landing + change-password form.

    FreeIPA often requires a password change when the password is expired.
    This uses python-freeipa's `change_password` endpoint (does not require an authenticated session).
    """

    if request.user.is_authenticated:
        return redirect("home")

    initial_username = None
    try:
        initial_username = request.session.get("_freeipa_pwexp_username")
    except Exception:
        initial_username = None

    form = ExpiredPasswordChangeForm(request.POST or None, initial={"username": initial_username} if initial_username else None)
    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        current_password = form.cleaned_data["current_password"]
        otp = _normalize_str(form.cleaned_data.get("otp")) or None
        new_password = form.cleaned_data["new_password"]

        try:
            client = _build_freeipa_client()
            # python-freeipa signature: change_password(username, new_password, old_password, otp=None)
            client.change_password(username, new_password, current_password, otp=otp)

            try:
                request.session.pop("_freeipa_pwexp_username", None)
            except Exception:
                pass

            messages.success(request, "Password changed. Please log in.")
            return redirect("login")
        except exceptions.PWChangePolicyError as e:
            logger.debug("password_expired: policy error username=%s error=%s", username, e)
            form.add_error(None, "Password change rejected by policy. Please choose a stronger password.")
        except exceptions.PWChangeInvalidPassword as e:
            logger.debug("password_expired: invalid password username=%s error=%s", username, e)
            form.add_error("current_password", "Current password is incorrect.")
        except exceptions.PasswordExpired:
            # Still expired is fine; user is here to change it.
            form.add_error(None, "Password is expired; please change it below.")
        except exceptions.Unauthorized:
            form.add_error(None, "Unable to change password. Please check your username and current password.")
        except exceptions.FreeIPAError as e:
            logger.warning("password_expired: FreeIPA error username=%s error=%s", username, e)
            form.add_error(None, "Unable to change password due to a FreeIPA error.")
        except Exception as e:
            logger.exception("password_expired: unexpected error username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Unable to change password (debug): {e}")
            else:
                form.add_error(None, "Unable to change password due to an internal error.")

    return render(request, "core/password_expired.html", {"form": form})


def otp_sync(request: HttpRequest) -> HttpResponse:
    """Noggin-style OTP sync.

    This is intentionally *not* behind login: users may need it when their
    token has drifted and they can't log in.

    FreeIPA supports syncing via a special endpoint:
    POST https://<host>/ipa/session/sync_token
    with form data: user, password, first_code, second_code, token (optional).
    """

    if request.user.is_authenticated:
        return redirect("home")

    form = SyncTokenForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]
        first_code = form.cleaned_data["first_code"]
        second_code = form.cleaned_data["second_code"]
        token = _normalize_str(form.cleaned_data.get("token")) or None

        url = f"https://{settings.FREEIPA_HOST}/ipa/session/sync_token"
        data = {
            "user": username,
            "password": password,
            "first_code": first_code,
            "second_code": second_code,
            "token": token or "",
        }

        try:
            session = requests.Session()
            response = session.post(
                url=url,
                data=data,
                verify=settings.FREEIPA_VERIFY_SSL,
                timeout=10,
            )
            if response.ok and "Token sync rejected" not in (response.text or ""):
                messages.success(request, "Token successfully synchronized")
                return redirect("login")

            form.add_error(None, "The username, password or token codes are not correct.")
        except requests.exceptions.RequestException:
            form.add_error(None, "No IPA server available")
        except Exception as e:
            logger.exception("otp_sync: unexpected error username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Something went wrong (debug): {e}")
            else:
                form.add_error(None, "Something went wrong")

    return render(request, "core/sync_token.html", {"form": form})
