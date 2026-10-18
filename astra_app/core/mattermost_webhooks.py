import datetime
import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from django.conf import settings
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from core.elections_services import election_vote_url
from core.membership import PendingRenewal
from core.membership_dues import effective_dues_usd
from core.membership_notifications import (
    membership_extend_url,
    membership_requests_url,
)
from core.models import Ballot, MattermostWebhookEndpoint, MembershipRequest, MembershipTerminationFeedback
from core.public_urls import build_public_absolute_url
from core.signal_receivers import connect_once, safe_receiver
from core.signals import CANONICAL_SIGNALS
from core.tokens import election_genesis_chain_hash

logger = logging.getLogger("core.mattermost_webhooks")
_URL_RE = re.compile(r"https?://\S+")

_GREEN_EVENTS = {
    "account_invitation_accepted",
    "account_deletion_completed",
    "membership_request_approved",
    "organization_membership_request_approved",
    "election_tallied",
    "election_quorum_met",
    "organization_claimed",
    "organization_created",
}

_BLUE_EVENTS = {
    "election_opened",
    "membership_request_submitted",
    "organization_membership_request_submitted",
}

_ORANGE_EVENTS = {
    "account_deletion_pending_privilege_check",
    "account_deletion_approved",
    "membership_rfi_sent",
    "membership_rfi_replied",
    "organization_membership_rfi_sent",
    "organization_membership_rfi_replied",
    "election_deadline_extended",
    "membership_expiring_soon",
    "organization_membership_expiring_soon",
    "organization_membership_renewals_pending",
    "membership_self_terminated",
    "user_country_changed",
    "organization_country_changed",
    "user_embargoed_country_changed",
    "organization_embargoed_country_changed",
}

_RED_EVENTS = {
    "account_deletion_cancelled",
    "membership_request_rejected",
    "membership_request_rescinded",
    "organization_membership_request_rejected",
    "organization_membership_request_rescinded",
    "account_deletion_rejected",
    "membership_expired",
    "organization_membership_expired",
    "account_deletion_requested",
    "election_closed",
}

# Posted once per sponsorship, carrying the Membership itself.
_SPONSORSHIP_EXPIRY_EVENTS = {
    "organization_membership_expiring_soon",
    "organization_membership_expired",
}

_ACCOUNT_DELETION_EVENTS = {
    "account_deletion_requested",
    "account_deletion_pending_privilege_check",
    "account_deletion_approved",
    "account_deletion_rejected",
    "account_deletion_cancelled",
    "account_deletion_completed",
}

_MEMBERSHIP_REQUEST_DETAIL_EVENTS = {
    "membership_request_submitted",
    "membership_request_approved",
    "membership_request_rejected",
    "membership_request_rescinded",
    "membership_rfi_sent",
    "membership_rfi_replied",
    "organization_membership_request_submitted",
    "organization_membership_request_approved",
    "organization_membership_request_rejected",
    "organization_membership_request_rescinded",
    "organization_membership_rfi_sent",
    "organization_membership_rfi_replied",
}

_ACCOUNT_DELETION_TEMPLATE_VARIABLES = {
    # PRIVACY-RESTRICTED: This event exposes account_deletion_username (a plain username)
    # alongside a deletion intent signal. Route this event ONLY to a restricted
    # privacy/admin Mattermost channel. Do NOT route it to general #ops or
    # community-visible channels; doing so constitutes an unauthorized disclosure.
    "account_deletion_request": "AccountDeletionRequest object or equivalent.",
    "account_deletion_request_id": "Deletion request primary key.",
    "account_deletion_username": "Username that requested deletion. PRIVATE — see channel routing note above.",
    "account_deletion_status": "Deletion request status.",
    "account_deletion_manual_review_required": "Whether manual review is required.",
    "account_deletion_blocker_codes": "Blocker-code list for manual review.",
    "account_deletion_blocker_codes_csv": "Comma-separated blocker codes.",
}

_SPONSORSHIP_TEMPLATE_VARIABLES = {
    "membership": "Membership object (example: {{ membership.target_organization.pk }}).",
    "organization_id": "Sponsor organization primary key.",
    "organization_name": "Sponsor organization name.",
    "representative": "Username of the organization's representative (empty when unclaimed).",
    "membership_type_code": "Sponsorship level code (example: gold).",
    "membership_type_name": "Sponsorship level display name.",
    "expires_at": "Expiry datetime object.",
    "dues_usd": "Annual dues the sponsor pays, after any waiver.",
    "sponsor_notified": "True when the sponsor was emailed about it.",
}

_ACCOUNT_DELETION_EVENT_TEXT = {
    "account_deletion_requested": "Account deletion requested",
    "account_deletion_pending_privilege_check": "Account deletion pending privilege check",
    "account_deletion_approved": "Account deletion approved",
    "account_deletion_rejected": "Account deletion rejected",
    "account_deletion_cancelled": "Account deletion cancelled",
    "account_deletion_completed": "Account deletion completed",
}

_ELECTION_EVENT_TEXT = {
    "election_opened": "Election opened",
    "election_closed": "Election closed",
    "election_tallied": "Election tallied",
    "election_deadline_extended": "Election deadline extended",
    "election_quorum_met": "Election quorum reached",
}

# Events whose fields describe a membership request (or, for the count-only ones, a batch).
_MEMBERSHIP_EVENT_TEXT = {
    "membership_request_submitted": "Membership request submitted",
    "membership_request_approved": "Membership request approved",
    "membership_request_rejected": "Membership request rejected",
    "membership_request_rescinded": "Membership request rescinded",
    "membership_rfi_sent": "Membership request RFI sent",
    "membership_rfi_replied": "Membership request RFI replied",
    "membership_expiring_soon": "Memberships expiring soon",
    "membership_expired": "Memberships expired",
    "organization_membership_request_submitted": "Organization membership request submitted",
    "organization_membership_request_approved": "Organization membership request approved",
    "organization_membership_request_rejected": "Organization membership request rejected",
    "organization_membership_request_rescinded": "Organization membership request rescinded",
    "organization_membership_rfi_sent": "Organization membership request RFI sent",
    "organization_membership_rfi_replied": "Organization membership request RFI replied",
}

_COMMON_TEMPLATE_VARIABLES: dict[str, str] = {
    "actor": "Username that triggered the event (often empty for scheduled tasks).",
}

_EVENT_TEMPLATE_VARIABLES: dict[str, dict[str, str]] = {
    "account_invitation_accepted": {
        "account_invitation": "AccountInvitation object (example: {{ account_invitation.pk }}).",
        "account_invitation_id": "Account invitation primary key.",
        "account_invitation_email": "Invitation email address.",
        "account_invitation_full_name": "Invitation full name.",
        "account_invitation_accepted_username": "Accepted username when known.",
        "account_invitation_accepted_at": "Accepted timestamp object.",
        "account_invitation_accepted_at_iso": "Accepted timestamp as an ISO string.",
        "account_invitation_matched_usernames": "Matched FreeIPA usernames list.",
        "account_invitation_matched_usernames_csv": "Comma-separated matched usernames.",
        "account_invitation_organization_id": "Linked organization primary key when present.",
        "account_invitation_organization_name": "Linked organization display name when present.",
    },
    "election_opened": {
        "election": "Election object (example: {{ election.name }}, {{ election.pk }}).",
        "election_id": "Election primary key.",
        "election_name": "Election display name.",
        "election_end_datetime": "Election end datetime object.",
        "election_end_datetime_iso": "Election end datetime ISO string.",
        "election_genesis_hash": "Election genesis chain hash.",
    },
    "election_closed": {
        "election": "Election object (example: {{ election.name }}, {{ election.pk }}).",
        "election_id": "Election primary key.",
        "election_name": "Election display name.",
        "election_end_datetime": "Election end datetime object.",
        "election_end_datetime_iso": "Election end datetime ISO string.",
        "election_genesis_hash": "Election genesis chain hash.",
        "election_final_chain_hash": "Final chain hash for the closed election.",
    },
    "election_tallied": {
        "election": "Election object (example: {{ election.name }}, {{ election.pk }}).",
        "election_id": "Election primary key.",
        "election_name": "Election display name.",
        "election_end_datetime": "Election end datetime object.",
        "election_end_datetime_iso": "Election end datetime ISO string.",
        "election_genesis_hash": "Election genesis chain hash.",
        "election_winners": "List of winner usernames from tally_result.elected.",
        "election_winners_csv": "Comma-separated winner usernames.",
    },
    "election_deadline_extended": {
        "election": "Election object (example: {{ election.name }}, {{ election.pk }}).",
        "election_id": "Election primary key.",
        "election_name": "Election display name.",
        "election_end_datetime": "Election end datetime object.",
        "election_end_datetime_iso": "Election end datetime ISO string.",
        "election_genesis_hash": "Election genesis chain hash.",
        "previous_end_datetime": "Previous election deadline (datetime).",
        "new_end_datetime": "New election deadline (datetime).",
    },
    "election_quorum_met": {
        "election": "Election object (example: {{ election.name }}, {{ election.pk }}).",
        "election_id": "Election primary key.",
        "election_name": "Election display name.",
        "election_end_datetime": "Election end datetime object.",
        "election_end_datetime_iso": "Election end datetime ISO string.",
        "election_genesis_hash": "Election genesis chain hash.",
    },
    "membership_request_submitted": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
    },
    "membership_request_approved": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
    },
    "membership_request_rejected": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
    },
    "membership_request_rescinded": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
    },
    "membership_rfi_sent": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
    },
    "membership_rfi_replied": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
    },
    "membership_expiring_soon": {
        "count": "Number of queued notifications from the expiration job.",
        "membership_type": "Membership type selector used by the job (for current jobs: all).",
    },
    "membership_expired": {
        "count": "Number of expired memberships cleaned up by the job.",
        "membership_type": "Membership type selector used by the cleanup job (for current jobs: all).",
    },
    "membership_self_terminated": {
        "username": "Username of the member who self-terminated.",
        "membership_type": "MembershipType-like object when available.",
        "membership_type_code": "Membership type code.",
        "membership_type_name": "Membership type display name.",
        "termination_feedback_id": "MembershipTerminationFeedback primary key (see it in the Django admin).",
        "termination_reason_category": "Reason the member chose: privacy, no_longer_needed, duplicate or other.",
    },
    "account_deletion_requested": _ACCOUNT_DELETION_TEMPLATE_VARIABLES,
    "account_deletion_pending_privilege_check": _ACCOUNT_DELETION_TEMPLATE_VARIABLES,
    "account_deletion_approved": _ACCOUNT_DELETION_TEMPLATE_VARIABLES,
    "account_deletion_rejected": _ACCOUNT_DELETION_TEMPLATE_VARIABLES,
    "account_deletion_cancelled": _ACCOUNT_DELETION_TEMPLATE_VARIABLES,
    "account_deletion_completed": _ACCOUNT_DELETION_TEMPLATE_VARIABLES,
    "organization_membership_request_submitted": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
        "organization_id": "Target organization primary key.",
        "organization_display_name": "Target organization display name.",
    },
    "organization_membership_request_approved": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
        "organization_id": "Target organization primary key.",
        "organization_display_name": "Target organization display name.",
    },
    "organization_membership_request_rejected": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
        "organization_id": "Target organization primary key.",
        "organization_display_name": "Target organization display name.",
    },
    "organization_membership_request_rescinded": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
        "organization_id": "Target organization primary key.",
        "organization_display_name": "Target organization display name.",
    },
    "organization_membership_rfi_sent": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
        "organization_id": "Target organization primary key.",
        "organization_display_name": "Target organization display name.",
    },
    "organization_membership_rfi_replied": {
        "membership_request": "MembershipRequest object (example: {{ membership_request.pk }}).",
        "membership_request_id": "Membership request primary key.",
        "membership_type_code": "Membership type code (example: sponsorship).",
        "membership_type_name": "Membership type display name.",
        "membership_target_kind": "Target kind (user or organization).",
        "requested_username": "Requested username for user-target requests.",
        "requested_organization_id": "Target organization ID for org-target requests.",
        "requested_organization_name": "Target organization display name for org-target requests.",
        "organization_id": "Target organization primary key.",
        "organization_display_name": "Target organization display name.",
    },
    "organization_membership_expiring_soon": {
        **_SPONSORSHIP_TEMPLATE_VARIABLES,
        "days": "Days until the sponsorship expires.",
    },
    "organization_membership_expired": {
        **_SPONSORSHIP_TEMPLATE_VARIABLES,
        "cleanup_failed": "True when removing FreeIPA access failed; the sponsorship is kept and retried tomorrow.",
    },
    "organization_membership_renewals_pending": {
        "count": "Number of sponsorships kept past their term by an undecided renewal request.",
        "renewals": (
            "Pending renewals, most overdue first (example: {% for renewal in renewals %}"
            "{{ renewal.membership.target_organization.name }} #{{ renewal.membership_request.pk }} "
            "{{ renewal.term_ended_at|date:'Y-m-d' }}{% endfor %})."
        ),
    },
    "organization_claimed": {
        "organization": "Organization object (example: {{ organization.name }}, {{ organization.pk }}).",
    },
    "organization_created": {
        "organization": "Organization object (example: {{ organization.name }}, {{ organization.pk }}).",
    },
    "user_country_changed": {
        "username": "Username of the user whose country changed.",
        "old_country": "Previous ISO country code.",
        "new_country": "New ISO country code.",
        "actor": "Username of user who made the change.",
    },
    "user_embargoed_country_changed": {
        "username": "Username of the user whose country changed.",
        "old_country": "Previous ISO country code.",
        "new_country": "New ISO country code.",
        "actor": "Username of user who made the change.",
    },
    "organization_country_changed": {
        "organization": "Organization object (example: {{ organization.name }}, {{ organization.pk }}).",
        "old_country": "Previous ISO country code.",
        "new_country": "New ISO country code.",
        "actor": "Username of user who made the change.",
    },
    "organization_embargoed_country_changed": {
        "organization": "Organization object (example: {{ organization.name }}, {{ organization.pk }}).",
        "old_country": "Previous ISO country code.",
        "new_country": "New ISO country code.",
        "actor": "Username of user who made the change.",
    },
}


def template_variable_reference_text(event_keys: list[str] | None = None) -> str:
    selected_events = [key for key in sorted(set(event_keys or [])) if key in CANONICAL_SIGNALS]

    lines: list[str] = []
    lines.append("Common template variables:")
    for variable_name, description in _COMMON_TEMPLATE_VARIABLES.items():
        lines.append(f"- {variable_name}: {description}")

    if not selected_events:
        lines.append("")
        lines.append("Select one or more events to see event-specific variables here.")
        return "\n".join(lines)

    for event_key in selected_events:
        lines.append("")
        lines.append(f"Event: {event_key}")
        event_variables = _EVENT_TEMPLATE_VARIABLES.get(event_key, {})
        if not event_variables:
            lines.append("- (no additional event-specific variables)")
            continue
        for variable_name, description in event_variables.items():
            lines.append(f"- {variable_name}: {description}")

    return "\n".join(lines)


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _attachment_color_for_event(event_key: str) -> str:
    if event_key in _GREEN_EVENTS:
        return "#36a64f"
    if event_key in _BLUE_EVENTS:
        return "#0072c6"
    if event_key in _ORANGE_EVENTS:
        return "#ff8c00"
    if event_key in _RED_EVENTS:
        return "#cc0000"
    return "#0072c6"


def _iso(value: object) -> str:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value or "")


def _event_title(event_key: str) -> str:
    return event_key.replace("_", " ").title()


def _organization_detail_url(organization_id: int) -> str:
    return build_public_absolute_url(
        reverse("organization-detail", kwargs={"organization_id": organization_id}),
        on_missing="relative",
    )


def _membership_request_detail_url(membership_request_id: int) -> str:
    return build_public_absolute_url(
        reverse("membership-request-detail", kwargs={"pk": membership_request_id}),
        on_missing="relative",
    )


def _event_link(event_key: str, kwargs: dict[str, object]) -> str:
    if event_key == "account_invitation_accepted":
        return build_public_absolute_url(reverse("account-invitations"), on_missing="relative")

    if event_key.startswith("election_"):
        election = kwargs.get("election")
        if election is not None:
            return build_public_absolute_url(reverse("election-detail", args=[election.pk]), on_missing="relative")
        return build_public_absolute_url(reverse("elections"), on_missing="relative")

    if event_key in {"organization_claimed", "organization_created"}:
        organization = kwargs.get("organization")
        if organization is not None:
            return _organization_detail_url(organization.pk)
        return build_public_absolute_url(reverse("organizations"), on_missing="relative")

    if event_key in _SPONSORSHIP_EXPIRY_EVENTS:
        return _organization_detail_url(kwargs["organization_id"])

    if event_key in {"membership_expiring_soon", "membership_expired"}:
        membership_type = str(kwargs.get("membership_type") or "").strip()
        if membership_type:
            return membership_extend_url(membership_type_code=membership_type)
        return membership_requests_url()

    if event_key == "membership_self_terminated":
        return build_public_absolute_url(
            reverse("admin:core_membershipterminationfeedback_change", args=[kwargs["termination_feedback_id"]]),
            on_missing="relative",
        )

    if event_key in _ACCOUNT_DELETION_EVENTS:
        # Scheduled signals carry only the id; the template context fills it in from the object.
        account_deletion_request_id = kwargs.get("account_deletion_request_id")
        if account_deletion_request_id:
            return build_public_absolute_url(
                reverse("admin:core_accountdeletionrequest_change", args=[account_deletion_request_id]),
                on_missing="relative",
            )
        return build_public_absolute_url(
            reverse("admin:core_accountdeletionrequest_changelist"),
            on_missing="relative",
        )

    if event_key in _MEMBERSHIP_REQUEST_DETAIL_EVENTS:
        membership_request = kwargs.get("membership_request")
        if membership_request is None:
            raise ValueError("membership_request is required for membership-request Mattermost events")
        return _membership_request_detail_url(membership_request.pk)

    membership_request = kwargs.get("membership_request")
    if membership_request is not None:
        return _membership_request_detail_url(membership_request.pk)

    return membership_requests_url()


def _election_fields(kwargs: dict[str, object]) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    election = kwargs.get("election")
    actor = kwargs.get("actor") or "system"
    if election is not None:
        fields.append({"title": "Election", "value": election.name, "short": True})
        fields.append({"title": "Election ID", "value": str(election.pk), "short": True})
        fields.append({"title": "Vote URL", "value": election_vote_url(request=None, election=election), "short": False})
    fields.append({"title": "Actor", "value": actor, "short": True})

    if "previous_end_datetime" in kwargs:
        fields.append({"title": "Previous deadline", "value": _iso(kwargs["previous_end_datetime"]), "short": False})
    if "new_end_datetime" in kwargs:
        fields.append({"title": "New deadline", "value": _iso(kwargs["new_end_datetime"]), "short": False})

    return fields


def _membership_fields(kwargs: dict[str, object], *, org_targeted: bool) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    membership_request = kwargs.get("membership_request")
    actor = kwargs.get("actor") or "system"

    if membership_request is not None:
        fields.append({"title": "Request ID", "value": str(membership_request.pk), "short": True})
        fields.append({"title": "Membership type", "value": membership_request.membership_type_id, "short": True})
        if org_targeted:
            organization_name = kwargs.get("organization_display_name") or membership_request.organization_display_name
            fields.append({"title": "Organization", "value": organization_name, "short": True})
        else:
            fields.append({"title": "Username", "value": membership_request.requested_username, "short": True})
    else:
        count = kwargs.get("count")
        membership_type = kwargs.get("membership_type")
        if count is not None:
            fields.append({"title": "Count", "value": str(count), "short": True})
        if membership_type is not None:
            fields.append({"title": "Membership type", "value": str(membership_type), "short": True})

    fields.append({"title": "Actor", "value": actor, "short": True})
    return fields


def _organization_fields(kwargs: dict[str, object]) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    actor = kwargs.get("actor") or "system"
    organization = kwargs.get("organization")
    if organization is not None:
        fields.append({"title": "Organization", "value": organization.name, "short": True})
        fields.append({"title": "Organization ID", "value": str(organization.pk), "short": True})
    fields.append({"title": "Actor", "value": actor, "short": True})
    return fields


def _days_text(days: int) -> str:
    return "1 day" if days == 1 else f"{days} days"


def _sponsorship_fields(kwargs: dict[str, object]) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = [
        {"title": "Level", "value": kwargs["membership_type_name"], "short": True},
        {"title": "Expires", "value": f"{kwargs['expires_at']:%Y-%m-%d}", "short": True},
    ]
    if kwargs["representative"]:
        fields.append({"title": "Representative", "value": kwargs["representative"], "short": True})
    fields.append({"title": "Annual dues", "value": f"${kwargs['dues_usd']:,}", "short": True})
    # Bold, since an un-notified sponsor is the one the committee must contact itself.
    notified = "Yes" if kwargs["sponsor_notified"] else "**No**"
    fields.append({"title": "Sponsor notified", "value": notified, "short": True})
    return fields


def _renewals_pending_table(renewals: list[PendingRenewal]) -> str:
    now = timezone.now()
    rows = [
        "| Organization | Level | Term ended | Overdue | Request |",
        "| --- | --- | --- | --- | --- |",
    ]
    for renewal in renewals:
        organization = renewal.membership.target_organization
        membership_request = renewal.membership_request
        status = MembershipRequest.Status(membership_request.status).label
        rows.append(
            f"| [{organization.name}]({_organization_detail_url(organization.pk)})"
            f" | {renewal.membership.membership_type.name}"
            f" | {renewal.term_ended_at:%Y-%m-%d}"
            f" | {_days_text((now - renewal.term_ended_at).days)}"
            f" | [#{membership_request.pk}]({_membership_request_detail_url(membership_request.pk)}) {status} |"
        )
    return "\n".join(rows)


def _default_payload(event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    link = _event_link(event_key, kwargs)
    title = _event_title(event_key)
    color = _attachment_color_for_event(event_key)

    if event_key == "organization_membership_renewals_pending":
        count = kwargs["count"]
        noun = "renewal" if count == 1 else "renewals"
        return {
            "text": f"{count} sponsorship {noun} pending past expiry",
            "attachments": [
                {
                    "color": color,
                    "title": "Pending sponsorship renewals",
                    "title_link": link,
                    "text": _renewals_pending_table(kwargs["renewals"]),
                }
            ],
        }

    if event_key == "account_invitation_accepted":
        text = "Account invitation accepted"
        actor = kwargs.get("actor") or "system"
        fields: list[dict[str, object]] = []
        account_invitation = kwargs.get("account_invitation")
        if account_invitation is not None:
            fields.append({"title": "Invitation ID", "value": str(account_invitation.pk), "short": True})
            if account_invitation.email:
                fields.append({"title": "Email", "value": account_invitation.email, "short": True})
            if account_invitation.accepted_username:
                fields.append({"title": "Accepted username", "value": account_invitation.accepted_username, "short": True})
            if account_invitation.freeipa_matched_usernames:
                matched_usernames = ", ".join(account_invitation.freeipa_matched_usernames)
                fields.append({"title": "Matched usernames", "value": matched_usernames, "short": False})
            if account_invitation.organization is not None:
                fields.append({"title": "Organization", "value": account_invitation.organization.name, "short": True})
        fields.append({"title": "Actor", "value": actor, "short": True})
    elif event_key in _ELECTION_EVENT_TEXT:
        text = _ELECTION_EVENT_TEXT[event_key]
        fields = _election_fields(kwargs)
    elif event_key in _MEMBERSHIP_EVENT_TEXT:
        text = _MEMBERSHIP_EVENT_TEXT[event_key]
        fields = _membership_fields(kwargs, org_targeted=event_key.startswith("organization_"))
    elif event_key == "membership_self_terminated":
        text = "Membership self-terminated"
        actor = kwargs.get("actor") or "system"
        disp_username = str(kwargs.get("username") or "").strip()
        membership_type_code = str(kwargs.get("membership_type_code") or "").strip()
        membership_type_name = str(kwargs.get("membership_type_name") or "").strip()
        fields = []
        if disp_username:
            fields.append({"title": "Username", "value": disp_username, "short": True})
        if membership_type_name:
            fields.append({"title": "Membership", "value": membership_type_name, "short": True})
        elif membership_type_code:
            fields.append({"title": "Membership", "value": membership_type_code, "short": True})
        reason = MembershipTerminationFeedback.ReasonCategory(kwargs["termination_reason_category"]).label
        fields.append({"title": "Reason", "value": reason, "short": True})
        fields.append({"title": "Actor", "value": actor, "short": True})
    elif event_key == "organization_membership_expiring_soon":
        text = f"Sponsorship for {kwargs['organization_name']} expires in {_days_text(kwargs['days'])}"
        fields = _sponsorship_fields(kwargs)
    elif event_key == "organization_membership_expired":
        text = f"Sponsorship for {kwargs['organization_name']} expired"
        if kwargs["cleanup_failed"]:
            text += ", but removing its FreeIPA access failed; retrying tomorrow"
        fields = _sponsorship_fields(kwargs)
    elif event_key in _ACCOUNT_DELETION_EVENTS:
        text = _ACCOUNT_DELETION_EVENT_TEXT.get(event_key, _event_title(event_key))
        actor = kwargs.get("actor") or "system"
        request_id = str(kwargs.get("account_deletion_request_id") or "").strip()
        disp_username = str(kwargs.get("account_deletion_username") or "").strip()
        status = str(kwargs.get("account_deletion_status") or "").strip()
        manual_review_required = kwargs.get("account_deletion_manual_review_required")
        blocker_codes_csv = str(kwargs.get("account_deletion_blocker_codes_csv") or "").strip()
        fields = []
        if request_id:
            fields.append({"title": "Request ID", "value": request_id, "short": True})
        if disp_username:
            fields.append({"title": "Username", "value": disp_username, "short": True})
        if status:
            fields.append({"title": "Status", "value": status, "short": True})
        if manual_review_required is not None:
            fields.append({"title": "Manual review", "value": str(bool(manual_review_required)), "short": True})
        if blocker_codes_csv:
            fields.append({"title": "Blockers", "value": blocker_codes_csv, "short": False})
        fields.append({"title": "Actor", "value": actor, "short": True})
    elif event_key in {"organization_claimed", "organization_created"}:
        text = "Organization claimed" if event_key == "organization_claimed" else "Organization created"
        fields = _organization_fields(kwargs)
    elif event_key in {"organization_country_changed", "organization_embargoed_country_changed"}:
        text = (
            "Organization country changed to/from embargoed country"
            if event_key == "organization_embargoed_country_changed"
            else "Organization country changed"
        )
        fields = _organization_fields(kwargs)
        old_country = str(kwargs.get("old_country") or "").strip()
        new_country = str(kwargs.get("new_country") or "").strip()
        if old_country:
            fields.append({"title": "Old country", "value": old_country, "short": True})
        if new_country:
            fields.append({"title": "New country", "value": new_country, "short": True})
    elif event_key in {"user_country_changed", "user_embargoed_country_changed"}:
        text = (
            "User country changed to/from embargoed country"
            if event_key == "user_embargoed_country_changed"
            else "User country changed"
        )
        actor = kwargs.get("actor") or "system"
        disp_username = str(kwargs.get("username") or "").strip()
        old_country = str(kwargs.get("old_country") or "").strip()
        new_country = str(kwargs.get("new_country") or "").strip()
        fields = []
        if disp_username:
            fields.append({"title": "Username", "value": disp_username, "short": True})
        if old_country:
            fields.append({"title": "Old country", "value": old_country, "short": True})
        if new_country:
            fields.append({"title": "New country", "value": new_country, "short": True})
        fields.append({"title": "Actor", "value": actor, "short": True})
    else:
        text = title
        fields = [{"title": "Event", "value": event_key, "short": True}]

    election = kwargs.get("election")
    account_invitation = kwargs.get("account_invitation")
    membership_request = kwargs.get("membership_request")
    if event_key.startswith("election_"):
        if election is not None:
            title = f"Election {election.name}"
    elif event_key == "account_invitation_accepted":
        if account_invitation is not None and account_invitation.email:
            title = f"Account invitation {account_invitation.email}"
    elif event_key == "membership_self_terminated":
        membership_type_name = str(kwargs.get("membership_type_name") or "").strip()
        if membership_type_name:
            title = f"Membership {membership_type_name}"
    elif event_key in _ACCOUNT_DELETION_EVENTS:
        request_id = str(kwargs.get("account_deletion_request_id") or "").strip()
        if request_id:
            title = f"Deletion request {request_id}"
    elif event_key in _SPONSORSHIP_EXPIRY_EVENTS:
        title = kwargs["organization_name"]
    elif event_key.startswith(("membership_", "organization_membership_")):
        if membership_request is not None:
            title = f"Membership request {membership_request.pk}"

    return {
        "text": text,
        "attachments": [
            {
                "color": color,
                "title": title,
                "title_link": link,
                "fields": fields,
            }
        ],
    }


def _render_template(template_str: str, context: dict[str, object]) -> str:
    try:
        return Template(template_str).render(Context(context))
    except Exception as exc:
        logger.error(
            "mattermost.template_render_error",
            extra={
                "exc_type": type(exc).__name__,
                "exc_message": str(exc),
            },
            exc_info=True,
        )
        return ""


def _render_attachments_template(
    template_payload: object,
    context: dict[str, object],
) -> list[dict[str, object]] | None:
    try:
        rendered_json = _render_template(json.dumps(template_payload), context)
        if not rendered_json:
            return None
        parsed = json.loads(rendered_json)
        if not isinstance(parsed, list):
            raise ValueError("Rendered attachments must be a JSON list.")
        normalized: list[dict[str, object]] = []
        for item in parsed:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized
    except Exception as exc:
        logger.error(
            "mattermost.attachments_render_error",
            extra={
                "exc_type": type(exc).__name__,
                "exc_message": str(exc),
            },
            exc_info=True,
        )
        return None


def _extract_tallied_winner_usernames(tally_result: object) -> list[str]:
    if not isinstance(tally_result, dict):
        return []

    elected = tally_result.get("elected")
    if not isinstance(elected, list):
        return []

    winners: list[str] = []
    for item in elected:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                winners.append(normalized)
            continue

        if isinstance(item, dict):
            for key in ("freeipa_username", "username", "name"):
                candidate_name = str(item.get(key) or "").strip()
                if candidate_name:
                    winners.append(candidate_name)
                    break

    return list(dict.fromkeys(winners))


def _non_empty(values: dict[str, object]) -> dict[str, object]:
    """Drop empty values, so templates see them as missing rather than rendering "None"."""
    return {key: value for key, value in values.items() if value not in (None, "", [])}


def _membership_request_template_context(kwargs: dict[str, object]) -> dict[str, object]:
    membership_request = kwargs.get("membership_request")
    if membership_request is None:
        return {}

    return _non_empty(
        {
            "membership_request_id": membership_request.pk,
            "membership_type_code": membership_request.membership_type.code,
            "membership_type_name": membership_request.membership_type.name,
            "membership_target_kind": membership_request.target_kind.value,
            "requested_username": membership_request.requested_username,
            "requested_organization_id": membership_request.requested_organization_id,
            "requested_organization_name": membership_request.organization_display_name,
        }
    )


def _election_template_context(event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    election = kwargs.get("election")
    if election is None:
        return {}

    genesis_hash = election_genesis_chain_hash(election.pk)
    context: dict[str, object] = {
        "election_id": election.pk,
        "election_genesis_hash": genesis_hash,
        "election_name": election.name,
        "election_end_datetime": election.end_datetime,
        "election_end_datetime_iso": election.end_datetime.isoformat(),
    }
    if event_key == "election_closed":
        context["election_final_chain_hash"] = (
            Ballot.objects.latest_chain_head_hash_for_election(election=election) or genesis_hash
        )
    if event_key == "election_tallied":
        winners = _extract_tallied_winner_usernames(election.tally_result)
        context["election_winners"] = winners
        context["election_winners_csv"] = ", ".join(winners)
    return context


def _build_template_context(event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    context = dict(kwargs)
    context.update(_membership_request_template_context(kwargs))
    context.update(_election_template_context(event_key, kwargs))

    account_invitation = kwargs.get("account_invitation")
    if account_invitation is not None:
        accepted_at = account_invitation.accepted_at
        organization = account_invitation.organization
        context |= _non_empty(
            {
                "account_invitation_id": account_invitation.pk,
                "account_invitation_email": account_invitation.email,
                "account_invitation_full_name": account_invitation.full_name,
                "account_invitation_accepted_username": account_invitation.accepted_username,
                "account_invitation_accepted_at": accepted_at,
                "account_invitation_accepted_at_iso": accepted_at.isoformat() if accepted_at else None,
                "account_invitation_matched_usernames": account_invitation.freeipa_matched_usernames,
                "account_invitation_matched_usernames_csv": ", ".join(account_invitation.freeipa_matched_usernames),
                "account_invitation_organization_id": account_invitation.organization_id,
                "account_invitation_organization_name": organization.name if organization is not None else None,
            }
        )

    # Sponsorship expiry events: read off the instance, since an expired one's row is already deleted.
    membership = kwargs.get("membership")
    if membership is not None:
        organization = membership.target_organization
        context |= {
            "organization_id": organization.pk,
            "organization_name": organization.name,
            "representative": organization.representative,
            "membership_type_code": membership.membership_type.code,
            "membership_type_name": membership.membership_type.name,
            "expires_at": membership.expires_at,
            "dues_usd": effective_dues_usd(membership),
        }

    # Count-only events (expiring soon, expired) pass a type code string rather than the model.
    membership_type = kwargs.get("membership_type")
    if membership_type is not None and not isinstance(membership_type, str):
        context |= _non_empty({"membership_type_code": membership_type.code, "membership_type_name": membership_type.name})

    account_deletion_request = kwargs.get("account_deletion_request")
    if account_deletion_request is not None:
        context |= _non_empty(
            {
                "account_deletion_request_id": account_deletion_request.pk,
                "account_deletion_username": account_deletion_request.username,
                "account_deletion_status": account_deletion_request.status,
            }
        )
        context["account_deletion_manual_review_required"] = account_deletion_request.manual_review_required
        context["account_deletion_blocker_codes"] = account_deletion_request.blocker_codes
        context["account_deletion_blocker_codes_csv"] = ", ".join(account_deletion_request.blocker_codes)

    # Expose only the id and category: the object's free-text reason must not reach
    # Mattermost, which would keep it past the privacy cleanup window.
    termination_feedback = context.pop("termination_feedback", None)
    if termination_feedback is not None:
        context["termination_feedback_id"] = termination_feedback.pk
        context["termination_reason_category"] = termination_feedback.reason_category

    return context


def _apply_endpoint_overrides(
    endpoint: MattermostWebhookEndpoint,
    base_payload: dict[str, object],
    context: dict[str, object],
) -> dict[str, object]:
    payload = dict(base_payload)

    if endpoint.text:
        rendered_text = _render_template(endpoint.text, context)
        if rendered_text:
            payload["text"] = rendered_text

    if endpoint.attachments is not None:
        rendered_attachments = _render_attachments_template(endpoint.attachments, context)
        if rendered_attachments is not None:
            payload["attachments"] = rendered_attachments

    if isinstance(endpoint.props, dict):
        current_props = payload.get("props")
        merged_props: dict[str, object] = {}
        if isinstance(current_props, dict):
            merged_props.update(current_props)
        merged_props.update(endpoint.props)
        payload["props"] = merged_props

    if endpoint.priority is not None:
        payload["priority"] = endpoint.priority

    if endpoint.channel:
        payload["channel"] = endpoint.channel
    if endpoint.username:
        payload["username"] = endpoint.username
    if endpoint.icon_url:
        payload["icon_url"] = endpoint.icon_url

    return payload


def _build_payload(endpoint: MattermostWebhookEndpoint, event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    template_context = _build_template_context(event_key, kwargs)
    payload = _apply_endpoint_overrides(
        endpoint=endpoint,
        base_payload=_default_payload(event_key, template_context),
        context=template_context,
    )

    if event_key in _MEMBERSHIP_REQUEST_DETAIL_EVENTS:
        detail_link = _event_link(event_key, template_context)
        attachments = payload.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            payload["attachments"] = [{"title": _event_title(event_key), "title_link": detail_link, "fields": []}]
            return payload

        for attachment in attachments:
            if isinstance(attachment, dict):
                attachment["title_link"] = detail_link

    return payload


def build_admin_test_payload(
    endpoint: MattermostWebhookEndpoint,
    *,
    endpoint_label: str,
    timestamp_iso: str,
    actor: str,
    test_ref: str,
) -> dict[str, object]:
    context: dict[str, object] = {
        "actor": actor,
        "test_ref": test_ref,
        "endpoint_label": endpoint_label,
        "timestamp": timestamp_iso,
    }
    base_payload = {
        "text": f"[TEST] Astra notification test - endpoint: '{endpoint_label}' - {timestamp_iso}",
    }
    return _apply_endpoint_overrides(endpoint=endpoint, base_payload=base_payload, context=context)


def _sanitize_error(exc: Exception) -> str:
    """Return a safe transport error category without leaking exception text."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.TooManyRedirects):
        return "too_many_redirects"
    if isinstance(exc, requests.exceptions.RequestException):
        return "request_error"
    return "unexpected_error"


def _sanitize_response_excerpt(body_text: str, *, max_chars: int = 280) -> str:
    """Return a compact response excerpt safe for logs and admin messages."""
    compact_text = " ".join(body_text.split())
    redacted_text = _URL_RE.sub("[redacted-url]", compact_text)
    if not redacted_text:
        return "(empty response body)"
    if len(redacted_text) <= max_chars:
        return redacted_text
    return f"{redacted_text[:max_chars]}..."


def _default_icon_url() -> str | None:
    configured_icon = settings.MATTERMOST_WEBHOOK_DEFAULT_ICON_URL
    if not configured_icon:
        return None
    if configured_icon.startswith("http://") or configured_icon.startswith("https://"):
        return configured_icon
    return build_public_absolute_url(configured_icon, on_missing="relative")


def _apply_default_identity(payload: dict[str, object]) -> dict[str, object]:
    outbound_payload = dict(payload)

    default_username = settings.MATTERMOST_WEBHOOK_DEFAULT_USERNAME
    current_username = str(outbound_payload.get("username") or "").strip()
    if default_username and not current_username:
        outbound_payload["username"] = default_username

    current_icon = str(outbound_payload.get("icon_url") or "").strip()
    default_icon = _default_icon_url()
    if default_icon and not current_icon:
        outbound_payload["icon_url"] = default_icon

    return outbound_payload


def post_mattermost_payload(
    endpoint: MattermostWebhookEndpoint,
    payload: dict[str, object],
) -> tuple[bool, int | None, str, str | None, str | None]:
    if not endpoint.url.startswith("https://"):
        return False, None, "invalid_url", None, None

    try:
        outbound_payload = _apply_default_identity(payload)
        response = requests.post(
            endpoint.url,
            json=outbound_payload,
            timeout=settings.MATTERMOST_WEBHOOK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except Exception as exc:
        return False, None, _sanitize_error(exc), type(exc).__name__, None

    if response.status_code < 200 or response.status_code >= 300:
        return False, response.status_code, "http_error", None, _sanitize_response_excerpt(response.text)

    return True, response.status_code, "", None, None


def _post_to_endpoint(endpoint: MattermostWebhookEndpoint, payload: dict[str, object]) -> None:
    success, status_code, error, exc_type, response_excerpt = post_mattermost_payload(endpoint, payload)
    if success:
        return

    logger.error(
        "mattermost.post_failed endpoint_id=%s url_hash=%s status_code=%s error=%s exc_type=%s response_excerpt=%s",
        endpoint.pk,
        _url_hash(endpoint.url),
        status_code,
        error,
        exc_type,
        response_excerpt or "-",
        extra={
            "endpoint_id": endpoint.pk,
            "url_hash": _url_hash(endpoint.url),
            "status_code": status_code,
            "error": error,
            "exc_type": exc_type,
            "response_excerpt": response_excerpt,
        },
    )


# Positional-only: the forwarded signal kwargs may carry their own event_key (the debug sender adds one).
def dispatch_mattermost_event(event_key: str, /, **kwargs: object) -> None:
    try:
        if event_key not in CANONICAL_SIGNALS:
            logger.warning("mattermost.unknown_event_key", extra={"event_key": event_key})
            return

        raw_count = kwargs.get("count")
        numeric_count: int | None = None
        if raw_count is not None:
            try:
                numeric_count = int(raw_count)
            except (TypeError, ValueError):
                logger.warning(
                    "mattermost.invalid_event_count",
                    extra={"event_key": event_key, "count": raw_count},
                )
        if numeric_count is not None and numeric_count <= 0:
            logger.info(
                "mattermost.zero_count_event_suppressed",
                extra={"event_key": event_key, "count": raw_count},
            )
            return

        if event_key in _MEMBERSHIP_REQUEST_DETAIL_EVENTS and kwargs.get("membership_request") is None:
            logger.warning("mattermost.membership_request_missing_context", extra={"event_key": event_key})
            return

        endpoints = MattermostWebhookEndpoint.objects.filter(enabled=True)
        for endpoint in endpoints:
            try:
                if not isinstance(endpoint.events, list):
                    logger.error(
                        "mattermost.invalid_events_field",
                        extra={
                            "endpoint_id": endpoint.pk,
                            "url_hash": _url_hash(endpoint.url),
                            "event_key": event_key,
                        },
                    )
                    continue

                unknown_keys = [item for item in endpoint.events if isinstance(item, str) and item not in CANONICAL_SIGNALS]
                if unknown_keys:
                    logger.warning(
                        "mattermost.unknown_configured_event_keys",
                        extra={
                            "endpoint_id": endpoint.pk,
                            "url_hash": _url_hash(endpoint.url),
                            "unknown_keys": sorted(unknown_keys),
                        },
                    )

                if event_key not in endpoint.events:
                    continue

                payload = _build_payload(endpoint, event_key, dict(kwargs))
                _post_to_endpoint(endpoint, payload)
            except Exception as exc:
                logger.error(
                    "mattermost.dispatch_error",
                    extra={
                        "endpoint_id": endpoint.pk,
                        "url_hash": _url_hash(endpoint.url),
                        "event_key": event_key,
                        "exc_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
    except Exception:
        logger.exception(
            "mattermost.dispatch.error",
            extra={"event_key": event_key},
        )


_RECEIVER_FUNCTIONS: dict[str, Callable[..., None]] = {}

_dispatch_inline: ContextVar[bool] = ContextVar("mattermost_dispatch_inline", default=False)


@contextmanager
def dispatch_inline() -> Iterator[None]:
    """Post webhooks before each signal send returns.

    For scheduled jobs: their process exits after the last job, killing any
    daemon dispatch thread still waiting on Mattermost.
    """
    token = _dispatch_inline.set(True)
    try:
        yield
    finally:
        _dispatch_inline.reset(token)


@connect_once
def connect_mattermost_receivers() -> None:
    for event_key, signal in CANONICAL_SIGNALS.items():
        @safe_receiver(event_key)
        def _receiver(*args: object, __event_key: str = event_key, **kwargs: object) -> None:
            _ = args
            if _dispatch_inline.get():
                dispatch_mattermost_event(__event_key, **kwargs)
                return
            # Defer webhook dispatch to a background thread so it doesn't block the HTTP request.
            # This is critical for endpoints like membership RFI that trigger multiple webhooks
            # and would otherwise timeout waiting for Mattermost responses.
            thread = threading.Thread(
                target=dispatch_mattermost_event,
                args=(__event_key,),
                kwargs=dict(kwargs),
                daemon=True,
                name=f"mattermost-dispatch-{__event_key}",
            )
            thread.start()

        _RECEIVER_FUNCTIONS[event_key] = _receiver
        signal.connect(_receiver, dispatch_uid=f"core.mattermost_webhooks.{event_key}")
