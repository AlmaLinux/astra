from __future__ import annotations

from django.conf import settings

from core.build_info import get_build_sha
from core.models import MembershipRequest
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)


def membership_review(request) -> dict[str, object]:
    if not hasattr(request, "user"):
        # Some template-tag tests render templates with a minimal request object.
        return {
            "membership_can_add": False,
            "membership_can_change": False,
            "membership_can_delete": False,
            "membership_can_view": False,
            "send_mail_can_add": False,
            "membership_requests_pending_count": 0,
            "membership_requests_on_hold_count": 0,
        }

    user = request.user

    try:
        membership_can_add = bool(user.has_perm(ASTRA_ADD_MEMBERSHIP))
        membership_can_change = bool(user.has_perm(ASTRA_CHANGE_MEMBERSHIP))
        membership_can_delete = bool(user.has_perm(ASTRA_DELETE_MEMBERSHIP))
        membership_can_view = bool(user.has_perm(ASTRA_VIEW_MEMBERSHIP))
        send_mail_can_add = bool(user.has_perm(ASTRA_ADD_SEND_MAIL))
    except Exception:
        membership_can_add = False
        membership_can_change = False
        membership_can_delete = False
        membership_can_view = False
        send_mail_can_add = False

    # Requests UI + approve/reject/ignore is guarded by "add".
    pending_count = 0
    on_hold_count = 0
    if membership_can_add:
        pending_count = MembershipRequest.objects.filter(status=MembershipRequest.Status.pending).count()
        on_hold_count = MembershipRequest.objects.filter(status=MembershipRequest.Status.on_hold).count()

    return {
        "membership_can_add": membership_can_add,
        "membership_can_change": membership_can_change,
        "membership_can_delete": membership_can_delete,
        "membership_can_view": membership_can_view,
        "send_mail_can_add": send_mail_can_add,
        "membership_requests_pending_count": pending_count,
        "membership_requests_on_hold_count": on_hold_count,
    }


def organization_nav(request) -> dict[str, object]:
    if not hasattr(request, "user"):
        return {"has_organizations": False}

    user = request.user
    try:
        if not user.is_authenticated:
            return {"has_organizations": False}
        username = str(user.get_username() or "").strip()
    except Exception:
        return {"has_organizations": False}
    if not username:
        return {"has_organizations": False}

    # Users can self-serve creating organizations, so keep the navigation visible.
    return {"has_organizations": True}


def chat_networks(_request) -> dict[str, object]:
    return {"chat_networks": settings.CHAT_NETWORKS}


def build_info(_request) -> dict[str, object]:
    return {"build_sha": get_build_sha()}
