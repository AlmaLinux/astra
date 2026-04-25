import logging
import os
import time
from zoneinfo import ZoneInfo

import sentry_sdk
from django.conf import settings
from django.contrib.auth import get_user as django_get_user
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from config.logging_context import RequestLogContext, reset_request_log_context, set_request_log_context
from core.freeipa.circuit_breaker import _freeipa_circuit_open, _is_freeipa_availability_error
from core.freeipa.client import (
    clear_current_viewer_username,
    clear_freeipa_service_client_cache,
    set_current_viewer_username,
)
from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.user import DegradedFreeIPAUser, FreeIPAUser
from core.ipa_user_attrs import _first
from core.logging_extras import exception_log_fields
from core.views_utils import get_username, try_get_username_from_user

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("astra.access")
_ACCESS_LOG_TEMPLATE = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'


def _should_skip_access_log(request_path: str) -> bool:
    return request_path == "/_ci" or request_path.startswith("/_ci/")


def _format_access_log_timestamp() -> str:
    return timezone.now().strftime("[%d/%b/%Y:%H:%M:%S %z]")


def _response_body_size(response: HttpResponse | None) -> str:
    if response is None:
        return "-"

    explicit_length = str(response.get("Content-Length") or "").strip()
    if explicit_length:
        return explicit_length

    try:
        return str(len(response.content))
    except Exception:
        return "-"


def _build_access_log_atoms(
    request,
    *,
    status_code: int,
    response: HttpResponse | None,
    client_ip: str | None,
    user_id: str | None,
) -> dict[str, object]:
    query_string = str(request.META.get("QUERY_STRING") or "")
    request_path = request.path
    full_path = request.get_full_path()
    request_line = f"{request.method} {full_path} HTTP/1.1"
    referer = str(request.META.get("HTTP_REFERER") or "-")
    user_agent = str(request.META.get("HTTP_USER_AGENT") or "-")
    remote_host = client_ip or "-"
    auth_user = user_id or "-"
    timestamp = _format_access_log_timestamp()
    bytes_sent = _response_body_size(response)

    duration_seconds = float(request.META.get("astra.duration_seconds", 0.0) or 0.0)
    duration_us = int(round(duration_seconds * 1_000_000))

    atoms: dict[str, object] = {
        "h": remote_host,
        "l": "-",
        "u": auth_user,
        "t": timestamp,
        "r": request_line,
        "s": status_code,
        "b": bytes_sent,
        "B": bytes_sent,
        "f": referer,
        "a": user_agent,
        "m": request.method,
        "U": request_path,
        "q": query_string,
        "H": "HTTP/1.1",
        "M": int(round(duration_seconds * 1000)),
        "D": duration_us,
        "L": f"{duration_seconds:.6f}",
        "T": int(duration_seconds),
        "p": f"<{os.getpid()}>",
        "{x-forwarded-for}i": str(request.META.get("HTTP_X_FORWARDED_FOR") or remote_host),
    }

    for key, value in request.META.items():
        key_str = str(key)
        key_lower = key_str.lower()
        atoms[f"{{{key_lower}}}e"] = value

        if key_str.startswith("HTTP_"):
            header_name = key_str[5:].lower().replace("_", "-")
            atoms[f"{{{header_name}}}i"] = value

    for meta_key, header_name in (("CONTENT_TYPE", "content-type"), ("CONTENT_LENGTH", "content-length")):
        meta_value = request.META.get(meta_key)
        if meta_value:
            atoms[f"{{{header_name}}}i"] = meta_value

    if "{host}i" not in atoms:
        try:
            host_value = request.get_host()
        except Exception:
            host_value = str(request.META.get("SERVER_NAME") or "")
        if host_value:
            atoms["{host}i"] = host_value
            atoms.setdefault("{http_host}e", host_value)

    if response is not None:
        for header_name, header_value in response.headers.items():
            atoms[f"{{{header_name.lower()}}}o"] = header_value

    for request_attr in ("csrf_cookie_needs_update", "csrf_cookie"):
        if hasattr(request, request_attr):
            atoms[f"{{{request_attr}}}e"] = getattr(request, request_attr)

    return atoms


def _render_gunicorn_style_access_line(
    request,
    *,
    status_code: int,
    response: HttpResponse | None,
    client_ip: str | None,
    user_id: str | None,
) -> str:
    atoms = _build_access_log_atoms(
        request,
        status_code=status_code,
        response=response,
        client_ip=client_ip,
        user_id=user_id,
    )
    return _ACCESS_LOG_TEMPLATE % atoms


def _get_user_timezone_name(user) -> str | None:
    # _user_data is optional: only FreeIPA-backed users have it.
    data = getattr(user, "_user_data", None)
    if not isinstance(data, dict):
        return None
    tz = _first(data, "fasTimezone")
    return str(tz).strip() or None if tz else None


def _wants_json_response(request) -> bool:
    """True when the client expects a JSON response (API call, .json endpoint)."""
    accept = str(request.headers.get("Accept") or "")
    content_type = str(request.content_type or "")
    return (
        request.path.endswith(".json")
        or "application/json" in accept
        or content_type.startswith("application/json")
    )


def _request_client_ip(request) -> str | None:
    forwarded_for = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded_for:
        first_ip = forwarded_for.split(",", maxsplit=1)[0].strip()
        if first_ip:
            return first_ip

    remote_addr = str(request.META.get("REMOTE_ADDR") or "").strip()
    return remote_addr or None


def _get_freeipa_or_default_user(request):
    # Prefer Django's standard session-based user restoration first.
    user = django_get_user(request)
    if getattr(user, "is_authenticated", False):
        return user

    # If this is a FreeIPA session, we store the username directly so it survives reloads.
    try:
        username = request.session.get("_freeipa_username")
    except Exception:
        username = None

    if username:
        try:
            freeipa_user = FreeIPAUser.get(username)
        except Exception as exc:
            if isinstance(exc, FreeIPAUnavailableError) or _is_freeipa_availability_error(exc):
                return DegradedFreeIPAUser(username)
            raise
        return freeipa_user if freeipa_user is not None else AnonymousUser()

    return user


class FreeIPAAuthenticationMiddleware:
    """Authentication middleware that can restore FreeIPA users without a DB row.

    Django's default AuthenticationMiddleware restores the user via backend.get_user(user_id).
    Our backend historically needed an in-memory id->username cache; this middleware prefers the
    username stored in session during login.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # If an upstream middleware already attached an authenticated user,
        # preserve it. Otherwise, wrap request.user so we can restore a FreeIPA
        # user from session-stored username even if Django resolves to
        # AnonymousUser (e.g. when no DB row exists).
        upstream_user = getattr(request, "user", None)
        upstream_is_authenticated = False
        if upstream_user is not None and not isinstance(upstream_user, SimpleLazyObject):
            try:
                upstream_is_authenticated = bool(getattr(upstream_user, "is_authenticated", False))
            except Exception:
                upstream_is_authenticated = False

        # Read session username once; used for both user restoration and viewer context.
        try:
            session_username: str | None = request.session.get("_freeipa_username")
        except Exception:
            session_username = None

        if not upstream_is_authenticated:
            if session_username and _freeipa_circuit_open():
                request.user = DegradedFreeIPAUser(session_username)
            else:
                request.user = SimpleLazyObject(lambda: _get_freeipa_or_default_user(request))

        # Expose the viewer username to the FreeIPAUser ingestion boundary so
        # privacy redaction (fasIsPrivate) can happen at initialization time.
        #
        # Important: do not force evaluation of `request.user` here when it's a
        # SimpleLazyObject; that evaluation may trigger FreeIPAUser.get() before
        # the viewer context is set.
        viewer_username = get_username(request, allow_user_fallback=False) or None
        if not viewer_username and upstream_is_authenticated:
            viewer_username = try_get_username_from_user(upstream_user) or None
        set_current_viewer_username(viewer_username)

        # Activate the user's timezone for this request so template tags/filters
        # (and timezone.localtime) reflect the user's configured FreeIPA timezone.
        activated = False
        try:
            tz_name = None
            try:
                user = request.user
            except Exception as exc:
                if session_username and _is_freeipa_availability_error(exc):
                    user = DegradedFreeIPAUser(session_username)
                    request.user = user
                else:
                    raise

            if getattr(user, "is_authenticated", False):
                if isinstance(user, DegradedFreeIPAUser):
                    return self.get_response(request)
                tz_name = _get_user_timezone_name(user)

            if not tz_name:
                tz_name = settings.TIME_ZONE

            try:
                timezone.activate(ZoneInfo(tz_name))
            except Exception:
                timezone.activate(ZoneInfo("UTC"))

            activated = True
            return self.get_response(request)
        finally:
            if activated:
                timezone.deactivate()
            clear_current_viewer_username()


class FreeIPAServiceClientReuseMiddleware:
    """Request-scoped reuse of the FreeIPA service client.

    Service-account operations can happen multiple times per request
    (profile page + groups + permissions, etc.). Reusing the logged-in client
    reduces repeated logins, but we must prevent reuse across requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Default to reusing the service client across requests (per worker
        # thread) to cut down on repeated admin logins. If you run an async
        # server with concurrent requests in the same thread and see issues,
        # set FREEIPA_SERVICE_CLIENT_REUSE_ACROSS_REQUESTS=0.
        if not settings.FREEIPA_SERVICE_CLIENT_REUSE_ACROSS_REQUESTS:
            clear_freeipa_service_client_cache()
        try:
            return self.get_response(request)
        finally:
            if not settings.FREEIPA_SERVICE_CLIENT_REUSE_ACROSS_REQUESTS:
                clear_freeipa_service_client_cache()


class SentryRequestContextMiddleware:
    """Attach searchable per-request metadata to Sentry logs/events."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        client_ip = _request_client_ip(request)
        request_id = str(request.META.get("HTTP_X_REQUEST_ID") or "").strip() or None

        user_id: str | None = None
        if request.user.is_authenticated:
            user_id = try_get_username_from_user(request.user)
            if user_id:
                sentry_sdk.set_user({"id": user_id, "username": user_id})
            else:
                sentry_sdk.set_user(None)
        else:
            sentry_sdk.set_user(None)

        if client_ip:
            sentry_sdk.set_tag("client_ip", client_ip)
        if request_id:
            sentry_sdk.set_tag("request_id", request_id)

        context_token = set_request_log_context(
            RequestLogContext(
                client_ip=client_ip,
                user_id=user_id,
                request_id=request_id,
                request_path=request.path,
                request_method=request.method,
            )
        )
        try:
            return self.get_response(request)
        finally:
            reset_request_log_context(context_token)


class StructuredAccessLogMiddleware:
    """Emit structured, user-aware access logs from Django request context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started_at = time.monotonic()
        response = None
        error: Exception | None = None

        try:
            response = self.get_response(request)
            return response
        except Exception as exc:  # noqa: BLE001 - re-raised after observability log
            error = exc
            raise
        finally:
            duration_seconds = time.monotonic() - started_at
            duration_ms = int(round(duration_seconds * 1000))
            request.META["astra.duration_seconds"] = duration_seconds
            if not _should_skip_access_log(request.path):
                status_code = response.status_code if response is not None else 500
                if status_code >= 500:
                    outcome = "server_error"
                elif status_code >= 400:
                    outcome = "client_error"
                else:
                    outcome = "success"

                extra: dict[str, int | str] = {
                    "event": "astra.http.access",
                    "component": "http",
                    "outcome": outcome,
                    "http_status": status_code,
                    "request_method": request.method,
                    "request_path": request.path,
                    "duration_ms": duration_ms,
                }

                request_query = str(request.META.get("QUERY_STRING") or "").strip()
                if request_query:
                    extra["request_query"] = request_query

                request_id = str(request.META.get("HTTP_X_REQUEST_ID") or "").strip()
                if request_id:
                    extra["request_id"] = request_id

                client_ip = _request_client_ip(request)
                if client_ip:
                    extra["client_ip"] = client_ip

                user_id = try_get_username_from_user(request.user) if request.user.is_authenticated else None
                if user_id:
                    extra["user_id"] = user_id

                if error is not None:
                    extra |= exception_log_fields(error)

                access_atoms = _build_access_log_atoms(
                    request,
                    status_code=status_code,
                    response=response,
                    client_ip=client_ip,
                    user_id=user_id,
                )
                access_logger.info(_ACCESS_LOG_TEMPLATE, access_atoms, extra=extra)


class LoginRequiredMiddleware:
    """Require an authenticated user for most pages.

    Exemptions:
    - Auth flows (login/logout/password reset)
    - Registration flow
    - SES webhook
    - Django admin and static/media
    - Election public exports (ballots/audit JSON)

    For JSON endpoints, return a JSON 403 instead of redirecting.
    """

    def __init__(self, get_response):
        self.get_response = get_response

        self._allowed_prefixes: tuple[str, ...] = (
            settings.STATIC_URL,
            settings.MEDIA_URL,
            "/_ci/",
            "/register",
            "/password-reset",
            "/admin/",
            "/api/v1/elections/ballot/verify",
            "/elections/ballot/verify",
            "/ses/event-webhook/",
            "/agreements/",
        )
        # Exact-match paths that must not be treated as prefixes.
        self._allowed_exact: frozenset[str] = frozenset({
            "/login",
            "/logout",
            "/otp/sync",
            "/password-expired",
            "/robots.txt",
            "/favicon.ico",
            "/healthz",
            "/readyz",
            "/privacy-policy",
            "/coc",
        })

    def __call__(self, request):
        path = request.path

        # Allow webhook/static/admin and auth-related URLs.
        if path.rstrip('/') in self._allowed_exact or any(path.startswith(p) for p in self._allowed_prefixes):
            return self.get_response(request)

        # Keep election public exports public (auditable public artifacts).
        if path.startswith("/elections/") and "/public/" in path and path.endswith(".json"):
            return self.get_response(request)

        if path.startswith("/api/v1/elections/") and "/public/" in path:
            return self.get_response(request)

        if request.user.is_authenticated:
            return self.get_response(request)

        # For JSON endpoints, avoid redirecting (clients expect JSON).
        if _wants_json_response(request):
            return JsonResponse({"ok": False, "error": "Authentication required."}, status=403)

        return redirect(f"{settings.LOGIN_URL}?next={request.get_full_path()}")


class FreeIPAUnavailableMiddleware(MiddlewareMixin):
    """Render a friendly 503 when FreeIPA is unavailable."""

    def process_exception(self, request, exception):
        if not (
            isinstance(exception, FreeIPAUnavailableError)
            or _is_freeipa_availability_error(exception)
        ):
            return None

        logger.warning(
            "FreeIPA unavailable during request path=%s",
            request.path,
            exc_info=False,
            extra={
                "event": "astra.freeipa.unavailable",
                "component": "middleware",
                "outcome": "error",
                "request_path": request.path,
            }
            | exception_log_fields(exception),
        )
        if _wants_json_response(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "AlmaLinux Accounts is temporarily unavailable. Please try again later.",
                },
                status=503,
            )

        html = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Service Unavailable</title></head><body>"
            "<main style=\"max-width:40rem;margin:4rem auto;font-family:sans-serif;\">"
            "<h1>Service unavailable</h1>"
            "<p>AlmaLinux Accounts is temporarily unavailable. Please try again later.</p>"
            "</main></body></html>"
        )
        return HttpResponse(html, status=503)
