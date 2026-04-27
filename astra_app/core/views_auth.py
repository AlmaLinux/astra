import hashlib
import hmac
import logging
import secrets
from typing import cast, override
from urllib.parse import quote

import requests
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core import signing
from django.forms.forms import NON_FIELD_ERRORS
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
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
from core.logging_extras import exception_log_fields
from core.middleware import _request_client_ip
from core.rate_limit import allow_request
from core.tokens import make_password_reset_token, read_password_reset_token
from core.views_utils import _normalize_str, get_username

from .forms_auth import (
    ExpiredPasswordChangeForm,
    FreeIPAAuthenticationForm,
    PasswordResetRequestForm,
    PasswordResetSetForm,
    SyncTokenForm,
)
from .password_reset import (
    find_user_for_password_reset,
    normalize_last_password_change,
    send_password_reset_email,
    send_password_reset_success_email,
)

logger = logging.getLogger(__name__)
PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY = "pending_account_invitation_token"
_AUTH_RECOVERY_ALLOWED_FIELD_ATTRS = {
    "autocomplete",
    "autocapitalize",
    "autocorrect",
    "inputmode",
    "maxlength",
    "minlength",
    "pattern",
    "placeholder",
    "spellcheck",
}


def _auth_recovery_json(value: object) -> str:
    return signing.json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def _auth_recovery_json_response(payload: dict[str, object], *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "private, no-cache"
    return response


def _auth_recovery_field_widget_type(widget: forms.Widget) -> str:
    if isinstance(widget, forms.HiddenInput):
        return "hidden"
    if isinstance(widget, forms.EmailInput):
        return "email"
    if isinstance(widget, forms.PasswordInput):
        return "password"
    return "text"


def _serialize_auth_recovery_form_field(*, bound_field: forms.BoundField) -> dict[str, object]:
    widget = bound_field.field.widget
    widget_attrs = bound_field.build_widget_attrs(widget.attrs)
    widget_context = widget.get_context(bound_field.html_name, bound_field.value(), widget_attrs)["widget"]
    attrs = {
        name: str(value)
        for name, value in widget_context["attrs"].items()
        if value is not None and name in _AUTH_RECOVERY_ALLOWED_FIELD_ATTRS
    }
    payload: dict[str, object] = {
        "name": bound_field.name,
        "id": bound_field.id_for_label,
        "widget": _auth_recovery_field_widget_type(widget),
        "value": "" if widget_context.get("value") is None else str(widget_context.get("value")),
        "required": bool(bound_field.field.required),
        "disabled": bool(bound_field.field.disabled),
        "errors": [str(error) for error in bound_field.errors],
        "attrs": attrs,
    }
    return payload


def _serialize_auth_recovery_form(
    *,
    form: PasswordResetRequestForm | PasswordResetSetForm | ExpiredPasswordChangeForm | SyncTokenForm,
    exclude_hidden_fields: bool = False,
) -> dict[str, object]:
    fields = [_serialize_auth_recovery_form_field(bound_field=field) for field in form]
    if exclude_hidden_fields:
        fields = [field for field in fields if field["widget"] != "hidden"]
    return {
        "is_bound": form.is_bound,
        "non_field_errors": [str(error) for error in form.non_field_errors()],
        "fields": fields,
    }


def _build_password_reset_request_payload(*, form: PasswordResetRequestForm) -> dict[str, object]:
    return {"form": _serialize_auth_recovery_form(form=form)}


def _build_password_reset_confirm_payload(
    *,
    username: str,
    has_otp: bool,
    form: PasswordResetSetForm,
) -> dict[str, object]:
    return {
        "username": username,
        "has_otp": has_otp,
        "form": _serialize_auth_recovery_form(form=form, exclude_hidden_fields=True),
    }


def _build_password_expired_payload(*, form: ExpiredPasswordChangeForm) -> dict[str, object]:
    return {"form": _serialize_auth_recovery_form(form=form)}


def _build_otp_sync_payload(*, form: SyncTokenForm) -> dict[str, object]:
    return {"form": _serialize_auth_recovery_form(form=form)}


def _render_password_reset_request_page(
    request: HttpRequest,
    *,
    form: PasswordResetRequestForm,
    status: int = 200,
) -> HttpResponse:
    return render(
        request,
        "core/password_reset_request.html",
        {
            "form": form,
            "auth_recovery_password_reset_api_url": reverse("api-password-reset-detail"),
            "auth_recovery_password_reset_submit_url": reverse("password-reset"),
            "auth_recovery_password_reset_login_url": reverse("login"),
            "auth_recovery_password_reset_csrf_token": get_token(request),
            "auth_recovery_password_reset_initial_payload_json": _auth_recovery_json(
                _build_password_reset_request_payload(form=form)
            ),
        },
        status=status,
    )


def _password_reset_confirm_submit_url(*, token_string: str) -> str:
    return f'{reverse("password-reset-confirm")}?token={quote(token_string)}'


def _render_password_reset_confirm_page(
    request: HttpRequest,
    *,
    username: str,
    token_string: str,
    has_otp: bool,
    form: PasswordResetSetForm,
) -> HttpResponse:
    return render(
        request,
        "core/password_reset_confirm.html",
        {
            "form": form,
            "username": username,
            "token": token_string,
            "auth_recovery_password_reset_confirm_api_url": (
                f'{reverse("api-password-reset-confirm-detail")}?token={quote(token_string)}'
            ),
            "auth_recovery_password_reset_confirm_submit_url": _password_reset_confirm_submit_url(token_string=token_string),
            "auth_recovery_password_reset_confirm_login_url": reverse("login"),
            "auth_recovery_password_reset_confirm_csrf_token": get_token(request),
            "auth_recovery_password_reset_confirm_token": token_string,
            "auth_recovery_password_reset_confirm_initial_payload_json": _auth_recovery_json(
                _build_password_reset_confirm_payload(username=username, has_otp=has_otp, form=form)
            ),
        },
    )


def _render_password_expired_page(
    request: HttpRequest,
    *,
    form: ExpiredPasswordChangeForm,
    status: int = 200,
) -> HttpResponse:
    return render(
        request,
        "core/password_expired.html",
        {
            "form": form,
            "auth_recovery_password_expired_api_url": reverse("api-password-expired-detail"),
            "auth_recovery_password_expired_submit_url": reverse("password-expired"),
            "auth_recovery_password_expired_login_url": reverse("login"),
            "auth_recovery_password_expired_csrf_token": get_token(request),
            "auth_recovery_password_expired_initial_payload_json": _auth_recovery_json(
                _build_password_expired_payload(form=form)
            ),
        },
        status=status,
    )


def _render_otp_sync_page(
    request: HttpRequest,
    *,
    form: SyncTokenForm,
    status: int = 200,
) -> HttpResponse:
    return render(
        request,
        "core/sync_token.html",
        {
            "form": form,
            "auth_recovery_otp_sync_api_url": reverse("api-otp-sync-detail"),
            "auth_recovery_otp_sync_submit_url": reverse("otp-sync"),
            "auth_recovery_otp_sync_login_url": reverse("login"),
            "auth_recovery_otp_sync_csrf_token": get_token(request),
            "auth_recovery_otp_sync_initial_payload_json": _auth_recovery_json(_build_otp_sync_payload(form=form)),
        },
        status=status,
    )


def _password_reset_confirm_has_otp_tokens(username: str) -> bool:
    try:
        svc = FreeIPAUser.get_client()
        res = svc.otptoken_find(o_ipatokenowner=username, o_all=True)
        tokens = res.get("result", []) if isinstance(res, dict) else []
        return bool(tokens)
    except exceptions.NotFound:
        return False
    except AttributeError:
        return False
    except Exception:
        logger.debug("Password reset: OTP token lookup failed username=%s", username, exc_info=True)
        return False


def _load_password_reset_confirm_state(
    token_string: str,
) -> tuple[dict[str, object] | None, tuple[int, str] | None]:
    normalized_token = _normalize_str(token_string)
    if not normalized_token:
        logger.warning("Password reset confirm rejected: missing token")
        return None, (400, "No token provided.")

    try:
        token = read_password_reset_token(normalized_token)
    except signing.SignatureExpired:
        logger.warning("Password reset confirm rejected: token expired")
        return None, (400, "This password reset link has expired. Please request a new one.")
    except signing.BadSignature:
        logger.warning("Password reset confirm rejected: invalid token signature")
        return None, (400, "This password reset link is invalid. Please request a new one.")

    username = _normalize_str(token.get("u"))
    token_email = _normalize_str(token.get("e")).lower()
    token_lpc = normalize_last_password_change(token.get("lpc"))
    logger.info(
        "Password reset confirm token parsed username=%s token_email=%s has_lpc=%s",
        username,
        token_email,
        bool(token_lpc),
    )
    if not username:
        logger.warning(
            "Password reset confirm rejected: token missing username token_email=%s",
            token_email,
        )
        return None, (400, "This password reset link is invalid. Please request a new one.")

    user = find_user_for_password_reset(username)
    if user is None:
        logger.warning(
            "Password reset confirm rejected: user lookup failed username=%s token_email=%s",
            username,
            token_email,
        )
        return None, (400, "This password reset link is invalid. Please request a new one.")

    user_email = _normalize_str(user.email).lower()
    if token_email and user_email and token_email != user_email:
        logger.warning(
            "Password reset confirm rejected: token/user email mismatch username=%s token_email=%s user_email=%s",
            username,
            token_email,
            user_email,
        )
        return None, (400, "This password reset link is no longer valid. Please request a new one.")

    user_lpc = normalize_last_password_change(user.last_password_change)
    if token_lpc != user_lpc:
        logger.warning(
            "Password reset confirm rejected: token/user lpc mismatch "
            "username=%s token_email=%s user_email=%s token_lpc=%s user_lpc=%s",
            username,
            token_email,
            user_email,
            token_lpc,
            user_lpc,
        )
        return None, (400, "Your password has changed since you requested this link. Please request a new password reset email.")

    return {
        "token": token,
        "username": username,
        "user_email": user_email,
        "has_otp": _password_reset_confirm_has_otp_tokens(username),
    }, None


def password_reset_detail_api(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return _auth_recovery_json_response({"error": "Method not allowed."}, status=405)

    form = PasswordResetRequestForm()
    return _auth_recovery_json_response(_build_password_reset_request_payload(form=form))


def password_reset_confirm_detail_api(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return _auth_recovery_json_response({"error": "Method not allowed."}, status=405)

    rate_limit_error = _password_reset_confirm_rate_limit_error(request, token_string=request.GET.get("token") or "")
    if rate_limit_error is not None:
        return _auth_recovery_json_response({"error": rate_limit_error[1]}, status=rate_limit_error[0])

    state, error = _load_password_reset_confirm_state(request.GET.get("token") or "")
    if error is not None:
        return _auth_recovery_json_response({"error": error[1]}, status=error[0])

    if state is None:
        return _auth_recovery_json_response({"error": "Unable to load password reset form."}, status=400)

    form = PasswordResetSetForm(require_otp=bool(state["has_otp"]))
    return _auth_recovery_json_response(
        _build_password_reset_confirm_payload(
            username=str(state["username"]),
            has_otp=bool(state["has_otp"]),
            form=form,
        )
    )


def password_expired_detail_api(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return _auth_recovery_json_response({"error": "Method not allowed."}, status=405)

    initial_username = None
    try:
        initial_username = request.session.get("_freeipa_pwexp_username")
    except Exception:
        initial_username = None

    form = ExpiredPasswordChangeForm(initial={"username": initial_username} if initial_username else None)
    return _auth_recovery_json_response(_build_password_expired_payload(form=form))


def otp_sync_detail_api(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return _auth_recovery_json_response({"error": "Method not allowed."}, status=405)

    form = SyncTokenForm()
    return _auth_recovery_json_response(_build_otp_sync_payload(form=form))


def _rate_limit_client_ip(request: HttpRequest) -> str:
    return _normalize_str(_request_client_ip(request))


def _login_style_rate_limit_error(
    request: HttpRequest,
    *,
    scope: str,
    subject: str,
    endpoint: str,
    message: str,
) -> tuple[int, str] | None:
    normalized_subject = _normalize_str(subject).lower()
    client_ip = _rate_limit_client_ip(request)

    limit = settings.AUTH_RATE_LIMIT_LOGIN_LIMIT
    window_seconds = settings.AUTH_RATE_LIMIT_LOGIN_WINDOW_SECONDS
    if allow_request(
        scope=scope,
        key_parts=[client_ip, normalized_subject],
        limit=limit,
        window_seconds=window_seconds,
    ):
        return None

    _emit_rate_limit_denial_log(
        request,
        endpoint=endpoint,
        limit=limit,
        window_seconds=window_seconds,
        subject=normalized_subject,
    )
    return 429, message


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
        "http_method": request.method,
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


def _password_reset_confirm_rate_limit_error(
    request: HttpRequest,
    *,
    token_string: str,
) -> tuple[int, str] | None:
    normalized_token = _normalize_str(token_string)
    if not normalized_token:
        return None

    client_ip = _rate_limit_client_ip(request)
    limit = settings.AUTH_RATE_LIMIT_PASSWORD_RESET_LIMIT
    window_seconds = settings.AUTH_RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS
    if allow_request(
        scope="auth.password_reset_confirm_get",
        key_parts=[client_ip, normalized_token],
        limit=limit,
        window_seconds=window_seconds,
    ):
        return None

    endpoint = request.resolver_match.view_name if request.resolver_match is not None else request.path
    _emit_rate_limit_denial_log(
        request,
        endpoint=endpoint,
        limit=limit,
        window_seconds=window_seconds,
        subject=normalized_token,
    )
    return 429, "Too many password reset attempts. Please try again later."


def _auth_log_extra(
    *,
    event: str,
    outcome: str,
    username: str | None = None,
    endpoint: str | None = None,
    error: BaseException | None = None,
) -> dict[str, str]:
    payload: dict[str, str] = {
        "event": event,
        "component": "auth",
        "outcome": outcome,
    }
    if username:
        payload["username"] = username
    if endpoint:
        payload["endpoint"] = endpoint
    if error is not None:
        payload.update(exception_log_fields(error))
    return payload


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

            logger.info(
                "Login failed username=%s reason=rate_limited",
                submitted_username,
                extra=_auth_log_extra(
                    event="astra.auth.login.failed",
                    outcome="rate_limited",
                    username=submitted_username or None,
                    endpoint="login",
                ),
            )

            # Bypass FreeIPALoginView.form_invalid() so 429 throttling does not emit
            # invalid-credentials warnings.
            typed_form = cast(FreeIPAAuthenticationForm, form)
            response = auth_views.LoginView.form_invalid(self, typed_form)
            response.status_code = 429
            return response

        return super().post(request, *args, **kwargs)

    @override
    def get_success_url(self) -> str:
        next_url = self.get_redirect_url()
        if next_url:
            return next_url

        username = get_username(self.request)
        if username:
            return reverse("user-profile", kwargs={"username": username})

        return super().get_success_url()

    def form_invalid(self, form) -> HttpResponse:
        request: HttpRequest = self.request
        submitted_username = _normalize_str(request.POST.get("username")).lower()

        if getattr(request, "_freeipa_password_expired", False):
            logger.info(
                "Login failed username=%s reason=password_expired",
                submitted_username,
                extra=_auth_log_extra(
                    event="astra.auth.login.failed",
                    outcome="password_expired",
                    username=submitted_username or None,
                    endpoint="login",
                ),
            )
            return redirect("password-expired")

        msg = getattr(request, "_freeipa_auth_error", None)
        if msg:
            outcome = "invalid_credentials" if msg == "Invalid username or password." else "backend_auth_error"
            logger.warning(
                "Login failed username=%s reason=%s",
                submitted_username,
                outcome,
                extra=_auth_log_extra(
                    event="astra.auth.login.failed",
                    outcome=outcome,
                    username=submitted_username or None,
                    endpoint="login",
                ),
            )
            form.errors.pop(NON_FIELD_ERRORS, None)
            form.add_error(None, msg)
        elif request.method == "POST":
            logger.warning(
                "Login failed username=%s reason=invalid_credentials",
                submitted_username,
                extra=_auth_log_extra(
                    event="astra.auth.login.failed",
                    outcome="invalid_credentials",
                    username=submitted_username or None,
                    endpoint="login",
                ),
            )

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

            return _render_password_reset_request_page(request, form=form, status=429)

    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["username_or_email"]
        user = find_user_for_password_reset(identifier)
        if user is not None:
            username = _normalize_str(user.username)
            email = _normalize_str(user.email)
            last_password_change = normalize_last_password_change(user.last_password_change)
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
                except Exception as error:
                    logger.exception(
                        "Password reset email send failed username=%s",
                        username,
                        extra=_auth_log_extra(
                            event="astra.auth.password_reset.email_send_failed",
                            outcome="error",
                            username=username,
                            endpoint="password-reset",
                            error=error,
                        ),
                    )

        messages.success(
            request,
            "If an account exists for that username/email, a password reset email has been sent.",
        )
        return redirect("login")

    return _render_password_reset_request_page(request, form=form)


def password_reset_confirm(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    token_string = _normalize_str(request.POST.get("token") or request.GET.get("token"))
    rate_limit_error = _password_reset_confirm_rate_limit_error(request, token_string=token_string)
    if rate_limit_error is not None:
        messages.warning(request, rate_limit_error[1])
        return redirect("password-reset")

    state, error = _load_password_reset_confirm_state(token_string)
    if error is not None:
        messages.warning(request, error[1])
        return redirect("login" if error[1] == "No token provided." else "password-reset")

    if state is None:
        messages.warning(request, "This password reset link is invalid. Please request a new one.")
        return redirect("password-reset")

    token = cast(dict[str, object], state["token"])
    username = str(state["username"])
    user_email = str(state["user_email"])
    token_lpc = normalize_last_password_change(token.get("lpc"))

    logger.info(
        "Password reset confirm accepted username=%s method=%s has_lpc=%s",
        username,
        request.method,
        bool(token_lpc),
    )

    has_otp = bool(state["has_otp"])

    form = PasswordResetSetForm(request.POST or None, require_otp=has_otp)
    if request.method == "POST" and form.is_valid():
        new_password = form.cleaned_data["password"]
        otp = _normalize_str(form.cleaned_data.get("otp")) or None

        if has_otp and not otp:
            form.add_error("otp", "One-Time Password is required for this account.")
            return _render_password_reset_confirm_page(
                request,
                username=username,
                token_string=token_string,
                has_otp=has_otp,
                form=form,
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
                "u": username,
                "e": user_email,
                "lpc": refreshed_lpc,
            }
            invitation_token = _normalize_str(token.get("i"))
            if invitation_token:
                next_token_payload["i"] = invitation_token

            next_token = make_password_reset_token(next_token_payload)
            form.add_error("otp" if has_otp else None, "Incorrect value.")
            return _render_password_reset_confirm_page(
                request,
                username=username,
                token_string=next_token,
                has_otp=has_otp,
                form=form,
            )
        except exceptions.FreeIPAError as error:
            logger.exception(
                "Password reset failed username=%s",
                username,
                extra=_auth_log_extra(
                    event="astra.auth.password_reset.freeipa_error",
                    outcome="error",
                    username=username,
                    endpoint="password-reset-confirm",
                    error=error,
                ),
            )
            form.add_error(None, "Unable to reset password due to a FreeIPA error.")
        except Exception as error:
            logger.exception(
                "Password reset failed (unexpected) username=%s",
                username,
                extra=_auth_log_extra(
                    event="astra.auth.password_reset.unexpected_error",
                    outcome="error",
                    username=username,
                    endpoint="password-reset-confirm",
                    error=error,
                ),
            )
            if settings.DEBUG:
                form.add_error(None, f"Unable to reset password (debug): {error}")
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
            except Exception as error:
                logger.exception(
                    "Password reset success email send failed username=%s",
                    username,
                    extra=_auth_log_extra(
                        event="astra.auth.password_reset.success_email_send_failed",
                        outcome="error",
                        username=username,
                        endpoint="password-reset-confirm",
                        error=error,
                    ),
                )
            messages.success(request, "Password updated. Please log in.")
            logger.info("Password reset confirm completed username=%s", username)
            return redirect("login")

    return _render_password_reset_confirm_page(
        request,
        username=username,
        token_string=token_string,
        has_otp=has_otp,
        form=form,
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

        rate_limit_error = _login_style_rate_limit_error(
            request,
            scope="auth.password_expired",
            subject=username,
            endpoint=request.resolver_match.view_name if request.resolver_match is not None else "password-expired",
            message="Too many password change attempts. Please try again later.",
        )
        if rate_limit_error is not None:
            form.add_error(None, rate_limit_error[1])
            return _render_password_expired_page(request, form=form, status=rate_limit_error[0])

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
        except exceptions.FreeIPAError as error:
            logger.warning(
                "password_expired: FreeIPA error username=%s error=%s",
                username,
                error,
                extra=_auth_log_extra(
                    event="astra.auth.password_expired.freeipa_error",
                    outcome="error",
                    username=username,
                    endpoint="password-expired",
                    error=error,
                ),
            )
            form.add_error(None, "Unable to change password due to a FreeIPA error.")
        except Exception as error:
            logger.exception(
                "password_expired: unexpected error username=%s",
                username,
                extra=_auth_log_extra(
                    event="astra.auth.password_expired.unexpected_error",
                    outcome="error",
                    username=username,
                    endpoint="password-expired",
                    error=error,
                ),
            )
            if settings.DEBUG:
                form.add_error(None, f"Unable to change password (debug): {error}")
            else:
                form.add_error(None, "Unable to change password due to an internal error.")

    return _render_password_expired_page(request, form=form)


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

        rate_limit_error = _login_style_rate_limit_error(
            request,
            scope="auth.otp_sync",
            subject=username,
            endpoint=request.resolver_match.view_name if request.resolver_match is not None else "otp-sync",
            message="Too many OTP sync attempts. Please try again later.",
        )
        if rate_limit_error is not None:
            form.add_error(None, rate_limit_error[1])
            return _render_otp_sync_page(request, form=form, status=rate_limit_error[0])

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
        except Exception as error:
            logger.exception(
                "otp_sync: unexpected error username=%s",
                username,
                extra=_auth_log_extra(
                    event="astra.auth.otp_sync.unexpected_error",
                    outcome="error",
                    username=username,
                    endpoint="otp-sync",
                    error=error,
                ),
            )
            if settings.DEBUG:
                form.add_error(None, f"Something went wrong (debug): {error}")
            else:
                form.add_error(None, "Something went wrong")

    return _render_otp_sync_page(request, form=form)
