"""Canonical wire shapes for the membership panel.

The user profile and the organization detail page render the same membership
panel: an active-membership list with renewal / tier-change / expiry-management
actions, plus a pending-request list. Both used to serialize those entries
independently, which is how the organization page silently shipped without a
renewal CTA for months -- the concept existed twice under different key names,
so nothing flagged the missing flag.

Both surfaces now build their entries here, and
``tests/test_membership_payload_contract.py`` locks the two payloads to
``MEMBERSHIP_ENTRY_CAPABILITY_KEYS`` so a capability added to one surface
cannot silently skip the other.
"""

from datetime import datetime
from typing import Final

from core.models import MembershipType

# Every capability flag a membership entry exposes. Both surfaces must emit all
# of these; the contract test fails if one drifts.
MEMBERSHIP_ENTRY_CAPABILITY_KEYS: Final[frozenset[str]] = frozenset({
    "canRenew",
    "canRequestTierChange",
    "canManage",
})


def serialize_datetime(value: object) -> str | None:
    """Serialize a datetime (or pre-serialized ISO string) for the wire."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def serialize_membership_type(membership_type: MembershipType | dict[str, object]) -> dict[str, str]:
    if isinstance(membership_type, MembershipType):
        return {
            "name": membership_type.name,
            "code": membership_type.code,
            "description": membership_type.description,
        }
    if isinstance(membership_type, dict):
        return {
            "name": str(membership_type.get("name", "")),
            "code": str(membership_type.get("code", "")),
            "description": str(membership_type.get("description", "")),
        }
    raise TypeError("Expected MembershipType data")


def serialize_membership_entry(
    *,
    membership_type: MembershipType | dict[str, object],
    created_at: object,
    expires_at: object,
    request_id: object,
    is_expiring_soon: bool,
    has_pending_request_in_category: bool,
    can_request_tier_change: bool,
    tier_change_membership_type_code: str,
    can_act: bool,
    can_view: bool,
    can_manage: bool,
) -> dict[str, object]:
    """Serialize one active membership for the shared membership panel.

    ``can_act`` is the "may initiate requests for this target" gate: the profile
    owner on the user profile, the representative or an ``ASTRA_ADD_MEMBERSHIP``
    holder on an organization. It gates both request-driven CTAs so a read-only
    reviewer never sees an action they cannot take.

    ``tier_change_membership_type_code`` is supplied by the caller because the
    two surfaces genuinely differ: the organization page pre-fills a *suggested*
    neighbouring tier, while the user profile pre-fills the held tier.
    """

    membership_type_data = serialize_membership_type(membership_type)
    code = membership_type_data["code"]

    can_renew = bool(can_act and is_expiring_soon and not has_pending_request_in_category)
    can_change_tier = bool(can_act and can_request_tier_change)

    return {
        "kind": "membership",
        "key": f"membership-{code}",
        "requestId": int(request_id) if request_id and (can_act or can_view) else None,
        "membershipType": membership_type_data,
        "createdAt": serialize_datetime(created_at),
        "expiresAt": serialize_datetime(expires_at),
        "isExpiringSoon": bool(is_expiring_soon),
        "canRenew": can_renew,
        # Renewal always re-requests the tier already held.
        "renewalMembershipTypeCode": code if can_renew else "",
        "canRequestTierChange": can_change_tier,
        "tierChangeMembershipTypeCode": str(tier_change_membership_type_code) if can_change_tier else "",
        "canManage": bool(can_manage),
    }


def serialize_pending_membership_entry(
    *,
    membership_type: MembershipType | dict[str, object],
    request_id: object,
    status: str,
    organization_name: str = "",
) -> dict[str, object]:
    """Serialize one pending membership request for the shared membership panel."""

    resolved_request_id = int(request_id) if request_id else 0
    return {
        "kind": "pending",
        "key": f"pending-{resolved_request_id}",
        "membershipType": serialize_membership_type(membership_type),
        "requestId": resolved_request_id,
        "status": str(status),
        "organizationName": str(organization_name or ""),
    }
