"""Custom Django signals for outbound event notifications.

Canonical event registry.
"""

from django.dispatch import Signal

# Elections
election_opened = Signal()
election_closed = Signal()
election_tallied = Signal()
election_deadline_extended = Signal()
election_quorum_met = Signal()

# Membership (user-targeted)
membership_request_submitted = Signal()
membership_request_approved = Signal()
membership_request_rejected = Signal()
membership_request_rescinded = Signal()
membership_rfi_sent = Signal()
membership_rfi_replied = Signal()
membership_expiring_soon = Signal()
membership_expired = Signal()

# Membership (org-targeted)
organization_membership_request_submitted = Signal()
organization_membership_request_approved = Signal()
organization_membership_request_rejected = Signal()
organization_membership_request_rescinded = Signal()
organization_membership_rfi_sent = Signal()
organization_membership_rfi_replied = Signal()

# Organizations
organization_claimed = Signal()
organization_created = Signal()

# Profile changes
user_country_changed = Signal()
organization_country_changed = Signal()

CANONICAL_SIGNALS: dict[str, Signal] = {
    "election_opened": election_opened,
    "election_closed": election_closed,
    "election_tallied": election_tallied,
    "election_deadline_extended": election_deadline_extended,
    "election_quorum_met": election_quorum_met,
    "membership_request_submitted": membership_request_submitted,
    "membership_request_approved": membership_request_approved,
    "membership_request_rejected": membership_request_rejected,
    "membership_request_rescinded": membership_request_rescinded,
    "membership_rfi_sent": membership_rfi_sent,
    "membership_rfi_replied": membership_rfi_replied,
    "membership_expiring_soon": membership_expiring_soon,
    "membership_expired": membership_expired,
    "organization_membership_request_submitted": organization_membership_request_submitted,
    "organization_membership_request_approved": organization_membership_request_approved,
    "organization_membership_request_rejected": organization_membership_request_rejected,
    "organization_membership_request_rescinded": organization_membership_request_rescinded,
    "organization_membership_rfi_sent": organization_membership_rfi_sent,
    "organization_membership_rfi_replied": organization_membership_rfi_replied,
    "organization_claimed": organization_claimed,
    "organization_created": organization_created,
    "user_country_changed": user_country_changed,
    "organization_country_changed": organization_country_changed,
}


class MembershipExpirationCommand:
    """Sentinel sender for membership expiration management-command signals."""
