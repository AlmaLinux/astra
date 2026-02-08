import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user as django_get_user
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from core.backends import (
    DegradedFreeIPAUser,
    FreeIPAUnavailableError,
    FreeIPAUser,
    _freeipa_circuit_open,
    _is_freeipa_availability_error,
    clear_current_viewer_username,
    clear_freeipa_service_client_cache,
    set_current_viewer_username,
)
from core.ipa_user_attrs import _first

logger = logging.getLogger(__name__)


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
        viewer_username: str | None = None
        try:
            if upstream_is_authenticated and hasattr(upstream_user, "get_username"):
                viewer_username = str(upstream_user.get_username()).strip() or None
        except Exception:
            viewer_username = None

        if not viewer_username and session_username:
            viewer_username = session_username
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
            "/admin/",
            "/login/",
            "/logout/",
            "/otp/sync/",
            "/password-reset/",
            "/password-expired/",
            "/register/",
            "/elections/ballot/verify/",
            "/ses/event-webhook/",
            "/privacy-policy/",
            "/robots.txt",
            "/favicon.ico",
            "/healthz",
            "/readyz",
        )

    def __call__(self, request):
        path = request.path

        # Allow webhook/static/admin and auth-related URLs.
        if any(path.startswith(p) for p in self._allowed_prefixes):
            return self.get_response(request)

        # Keep election public exports public (auditable public artifacts).
        if path.startswith("/elections/") and "/public/" in path and path.endswith(".json"):
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

        logger.warning("FreeIPA unavailable during request path=%s", request.path, exc_info=False)
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
