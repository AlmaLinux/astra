from django.conf import settings

from core.build_info import get_build_sha
from core.models import AccountInvitation, MembershipRequest
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)

# Map context key → permission codename for the membership review sidebar.
_MEMBERSHIP_PERMS: dict[str, str] = {
    "membership_can_add": ASTRA_ADD_MEMBERSHIP,
    "membership_can_change": ASTRA_CHANGE_MEMBERSHIP,
    "membership_can_delete": ASTRA_DELETE_MEMBERSHIP,
    "membership_can_view": ASTRA_VIEW_MEMBERSHIP,
    "send_mail_can_add": ASTRA_ADD_SEND_MAIL,
}


def membership_review(request) -> dict[str, object]:
    # Some template-tag tests render templates with a minimal request object.
    if not hasattr(request, "user"):
        return {
            **{k: False for k in _MEMBERSHIP_PERMS},
            "membership_requests_pending_count": 0,
            "membership_requests_on_hold_count": 0,
            "account_invitations_accepted_count": 0,
        }

    user = request.user
    try:
        perms = {key: bool(user.has_perm(perm)) for key, perm in _MEMBERSHIP_PERMS.items()}
    except Exception:
        perms = {key: False for key in _MEMBERSHIP_PERMS}

    # Requests UI + approve/reject/ignore is guarded by "add".
    pending_count = 0
    on_hold_count = 0
    accepted_invitations_count = 0
    if perms["membership_can_add"]:
        pending_count = MembershipRequest.objects.filter(status=MembershipRequest.Status.pending).count()
        on_hold_count = MembershipRequest.objects.filter(status=MembershipRequest.Status.on_hold).count()
        accepted_invitations_count = AccountInvitation.objects.filter(
            dismissed_at__isnull=True,
            accepted_at__isnull=False,
        ).count()

    return {
        **perms,
        "membership_requests_pending_count": pending_count,
        "membership_requests_on_hold_count": on_hold_count,
        "account_invitations_accepted_count": accepted_invitations_count,
    }


def organization_nav(request) -> dict[str, object]:
    """Navigation visibility for organizations; True for any authenticated user."""
    try:
        if hasattr(request, "user") and request.user.is_authenticated:
            if str(request.user.get_username() or "").strip():
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
    "membership-stats",
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
