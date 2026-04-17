from email.utils import parseaddr

from django.conf import settings

from core.build_info import get_build_sha
from core.freeipa.user import FreeIPAUser
from core.membership import visible_committee_membership_requests
from core.models import AccountInvitation, MembershipRequest
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
        pending_requests = list(
            MembershipRequest.objects.select_related("requested_organization")
            .filter(status=MembershipRequest.Status.pending)
            .order_by("requested_at", "pk")
        )
        on_hold_requests = list(
            MembershipRequest.objects.select_related("requested_organization")
            .filter(status=MembershipRequest.Status.on_hold)
            .order_by("on_hold_at", "requested_at", "pk")
        )
        lookup_usernames = {
            str(membership_request.requested_username or "").strip().lower()
            for membership_request in pending_requests + on_hold_requests
            if membership_request.is_user_target and str(membership_request.requested_username or "").strip()
        }
        live_users_by_username = FreeIPAUser.find_lightweight_by_usernames(lookup_usernames)
        perms["membership_requests_pending_count"] = len(
            visible_committee_membership_requests(
                pending_requests,
                live_usernames=live_users_by_username.keys(),
            )
        )
        perms["membership_requests_on_hold_count"] = len(
            visible_committee_membership_requests(
                on_hold_requests,
                live_usernames=live_users_by_username.keys(),
            )
        )
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
    return {
        "build_sha": get_build_sha(),
        "default_from_email_address": parseaddr(settings.DEFAULT_FROM_EMAIL)[1].strip(),
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
