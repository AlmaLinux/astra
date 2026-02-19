from django.conf import settings

from core.build_info import get_build_sha
from core.models import AccountInvitation, MembershipRequest
from core.permissions import (
    membership_review_permissions,
)
from core.views_utils import get_username


def membership_review(request) -> dict[str, object]:
    # Some template-tag tests render templates with a minimal request object.
    if not hasattr(request, "user"):
        return dict(membership_review_permissions(user=object()))

    user = request.user
    perms: dict[str, object] = dict(membership_review_permissions(user=user))

    # Requests UI + approve/reject/ignore is guarded by "add".
    if perms["membership_can_add"]:
        perms["membership_requests_pending_count"] = MembershipRequest.objects.filter(
            status=MembershipRequest.Status.pending
        ).count()
        perms["membership_requests_on_hold_count"] = MembershipRequest.objects.filter(
            status=MembershipRequest.Status.on_hold
        ).count()
        perms["account_invitations_accepted_count"] = AccountInvitation.objects.filter(
            dismissed_at__isnull=True,
            accepted_at__isnull=False,
        ).count()

    return perms


def organization_nav(request) -> dict[str, object]:
    """Navigation visibility for organizations; True for any authenticated user."""
    try:
        if hasattr(request, "user") and request.user.is_authenticated:
            if get_username(request):
                return {"has_organizations": True}
    except Exception:
        pass
    return {"has_organizations": False}


def chat_networks(_request) -> dict[str, object]:
    return {"chat_networks": settings.CHAT_NETWORKS}


def build_info(_request) -> dict[str, object]:
    return {"build_sha": get_build_sha()}


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
