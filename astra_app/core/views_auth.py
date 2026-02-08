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

from core.backends import FreeIPAUser, _build_freeipa_client
from core.models import AccountInvitation
from core.tokens import make_signed_token
from core.views_utils import _normalize_str

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


class FreeIPALoginView(auth_views.LoginView):
    """LoginView that can redirect / message based on FreeIPA backend signals."""

    template_name = "core/login.html"
    authentication_form = FreeIPAAuthenticationForm

    @override
    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    @override
    def get_success_url(self) -> str:
        user = getattr(self.request, "user", None)
        get_username = getattr(user, "get_username", None)
        if callable(get_username):
            try:
                username = str(get_username()).strip()
            except Exception:
                username = ""
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

        invite_token = _normalize_str(self.request.POST.get("invite") or self.request.GET.get("invite"))
        if not invite_token:
            return response

        try:
            payload = signing.loads(invite_token, salt=settings.SECRET_KEY)
        except signing.BadSignature:
            return response

        if not isinstance(payload, dict):
            return response

        invitation_id = payload.get("invitation_id")
        try:
            invitation_id = int(invitation_id)
        except (TypeError, ValueError):
            return response

        invitation = AccountInvitation.objects.filter(
            pk=invitation_id,
            invitation_token=invite_token,
            dismissed_at__isnull=True,
        ).first()
        if invitation is None:
            return response

        user = getattr(self.request, "user", None)
        get_username = getattr(user, "get_username", None)
        if not callable(get_username):
            return response

        try:
            username = str(get_username()).strip()
        except Exception:
            return response

        if not username:
            return response

        now = timezone.now()
        invitation.accepted_at = invitation.accepted_at or now
        usernames = set(invitation.freeipa_matched_usernames)
        usernames.add(username)
        invitation.freeipa_matched_usernames = sorted(usernames)
        invitation.freeipa_last_checked_at = now
        invitation.save(update_fields=["accepted_at", "freeipa_matched_usernames", "freeipa_last_checked_at"])

        return response


def password_reset_request(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["username_or_email"]
        user = find_user_for_password_reset(identifier)
        if user is not None:
            username = _normalize_str(user.username)
            email = _normalize_str(user.email)
            last_password_change = _normalize_str(user.last_password_change)
            if username and email:
                try:
                    send_password_reset_email(
                        request=request,
                        username=username,
                        email=email,
                        last_password_change=last_password_change,
                    )
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
            next_token = make_signed_token(
                {
                    "p": PASSWORD_RESET_TOKEN_PURPOSE,
                    "u": username,
                    "e": user_email,
                    "lpc": refreshed_lpc,
                }
            )
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
