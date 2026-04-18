from email.utils import parseaddr

import sentry_sdk
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse

from core.build_info import get_build_sha
from core.membership import get_membership_review_badge_counts
from core.models import AccountInvitation
from core.permissions import (
    membership_review_permissions,
)


def membership_review(request) -> dict[str, object]:
    # Some template-tag tests render templates with a minimal request object.
    if not hasattr(request, "user"):
        return dict(membership_review_permissions(user=object()))

    user = request.user
    perms: dict[str, object] = dict(membership_review_permissions(user=user))

    # Requests UI + approve/reject/ignore is guarded by "add".
    if perms["membership_can_add"]:
        badge_counts = get_membership_review_badge_counts()
        perms["membership_requests_pending_count"] = badge_counts["pending_count"]
        perms["membership_requests_on_hold_count"] = badge_counts["on_hold_count"]
        perms["account_invitations_accepted_count"] = AccountInvitation.objects.filter(
            dismissed_at__isnull=True,
            accepted_at__isnull=False,
        ).count()

    return perms


def organization_nav(request) -> dict[str, object]:
    """Navigation visibility for organizations; True for any authenticated user."""
    if hasattr(request, "user") and request.user.is_authenticated:
        return {"has_organizations": True}
    return {"has_organizations": False}


def chat_networks(_request) -> dict[str, object]:
    return {"chat_networks": settings.CHAT_NETWORKS}


def build_info(_request) -> dict[str, object]:
    sentry_browser_bundle_src = ""
    sentry_browser_config: dict[str, object] | None = None
    sentry_trace = ""
    sentry_baggage = ""
    if settings.SENTRY_DSN:
        sentry_browser_bundle_src = staticfiles_storage.url("core/vendor/sentry/bundle.tracing.min.js")
        sentry_browser_config = {
            "dsn": settings.SENTRY_DSN,
            "environment": settings.SENTRY_ENVIRONMENT,
            "release": settings.SENTRY_RELEASE,
            "tracesSampleRate": settings.SENTRY_TRACES_SAMPLE_RATE,
            "tunnel": reverse("sentry-browser-tunnel"),
        }
        sentry_trace = sentry_sdk.get_traceparent() or ""
        sentry_baggage = sentry_sdk.get_baggage() or ""

    return {
        "build_sha": get_build_sha(),
        "default_from_email_address": parseaddr(settings.DEFAULT_FROM_EMAIL)[1].strip(),
        "sentry_browser_bundle_src": sentry_browser_bundle_src,
        "sentry_browser_config": sentry_browser_config,
        "sentry_trace": sentry_trace,
        "sentry_baggage": sentry_baggage,
    }


_MEMBERSHIP_NAV_URLS = frozenset({
    "membership-requests", "account-invitations", "account-invitations-upload",
    "account-invitations-send", "membership-audit-log", "membership-audit-log-user",
    "membership-stats", "membership-sponsors",
})

_MAIL_NAV_URLS = frozenset({
    "email-templates", "email-template-create", "email-template-edit",
    "send-mail", "email-images",
})


def sidebar_active_flags(request) -> dict[str, bool]:
    """Pre-compute sidebar section active flags to avoid repeating URL checks in base.html."""
    resolver_match = getattr(request, "resolver_match", None)
    url_name = resolver_match.url_name if resolver_match else ""
    return {
        "membership_nav_active": url_name in _MEMBERSHIP_NAV_URLS,
        "mail_nav_active": url_name in _MAIL_NAV_URLS,
    }
