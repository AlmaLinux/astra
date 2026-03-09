import datetime
import hashlib
import json
import logging
import re
from collections.abc import Callable

import requests
from django.conf import settings
from django.template import Context, Template
from django.urls import reverse

from core.elections_services import election_vote_url
from core.membership_notifications import (
    membership_extend_url,
    membership_requests_url,
    organization_membership_request_url,
)
from core.models import Ballot, MattermostWebhookEndpoint
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
    "membership_self_terminated",
    "user_country_changed",
    "organization_country_changed",
}

_RED_EVENTS = {
    "account_deletion_cancelled",
    "membership_request_rejected",
    "membership_request_rescinded",
    "organization_membership_request_rejected",
    "organization_membership_request_rescinded",
    "account_deletion_rejected",
    "membership_expired",
    "account_deletion_requested",
    "election_closed",
}

_ACCOUNT_DELETION_EVENTS = {
    "account_deletion_requested",
    "account_deletion_pending_privilege_check",
    "account_deletion_approved",
    "account_deletion_rejected",
    "account_deletion_cancelled",
    "account_deletion_completed",
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

_ACCOUNT_DELETION_EVENT_TEXT = {
    "account_deletion_requested": "Account deletion requested",
    "account_deletion_pending_privilege_check": "Account deletion pending privilege check",
    "account_deletion_approved": "Account deletion approved",
    "account_deletion_rejected": "Account deletion rejected",
    "account_deletion_cancelled": "Account deletion cancelled",
    "account_deletion_completed": "Account deletion completed",
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
    "organization_country_changed": {
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


def _event_link(event_key: str, kwargs: dict[str, object]) -> str:
    if event_key == "account_invitation_accepted":
        return build_public_absolute_url(reverse("account-invitations"), on_missing="relative")

    if event_key.startswith("election_"):
        election = kwargs.get("election")
        if election is not None:
            try:
                return build_public_absolute_url(reverse("election-detail", args=[election.pk]), on_missing="relative")
            except Exception:
                return build_public_absolute_url(reverse("elections"), on_missing="relative")
        return build_public_absolute_url(reverse("elections"), on_missing="relative")

    if event_key.startswith("organization_membership_"):
        organization_id_obj = kwargs.get("organization_id")
        try:
            organization_id = int(organization_id_obj) if organization_id_obj is not None else 0
        except Exception:
            organization_id = 0
        if organization_id > 0:
            return organization_membership_request_url(organization_id=organization_id)
        return membership_requests_url()

    if event_key in {"organization_claimed", "organization_created"}:
        organization = kwargs.get("organization")
        if organization is not None:
            try:
                return build_public_absolute_url(
                    reverse("organization-detail", kwargs={"organization_id": organization.pk}),
                    on_missing="relative",
                )
            except Exception:
                return build_public_absolute_url(reverse("organizations"), on_missing="relative")
        return build_public_absolute_url(reverse("organizations"), on_missing="relative")

    if event_key in {"membership_expiring_soon", "membership_expired"}:
        membership_type = str(kwargs.get("membership_type") or "").strip()
        if membership_type:
            return membership_extend_url(membership_type_code=membership_type)
        return membership_requests_url()

    if event_key == "membership_self_terminated":
        return build_public_absolute_url(f"{reverse('settings')}?tab=membership", on_missing="relative")

    if event_key in _ACCOUNT_DELETION_EVENTS:
        return build_public_absolute_url(f"{reverse('settings')}?tab=privacy", on_missing="relative")

    membership_request = kwargs.get("membership_request")
    if membership_request is not None:
        try:
            return build_public_absolute_url(
                reverse("membership-request-detail", kwargs={"pk": membership_request.pk}),
                on_missing="relative",
            )
        except Exception:
            return membership_requests_url()

    return membership_requests_url()


def _election_fields(kwargs: dict[str, object]) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    election = kwargs.get("election")
    actor = str(kwargs.get("actor") or "system").strip() or "system"
    if election is not None:
        try:
            fields.append({"title": "Election", "value": str(election.name), "short": True})
            fields.append({"title": "Election ID", "value": str(election.pk), "short": True})
            fields.append({"title": "Vote URL", "value": election_vote_url(request=None, election=election), "short": False})
        except Exception:
            fields.append({"title": "Election", "value": str(election), "short": True})
    fields.append({"title": "Actor", "value": actor, "short": True})

    if "previous_end_datetime" in kwargs:
        fields.append({"title": "Previous deadline", "value": _iso(kwargs["previous_end_datetime"]), "short": False})
    if "new_end_datetime" in kwargs:
        fields.append({"title": "New deadline", "value": _iso(kwargs["new_end_datetime"]), "short": False})

    return fields


def _membership_fields(kwargs: dict[str, object], *, org_targeted: bool) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    membership_request = kwargs.get("membership_request")
    actor = str(kwargs.get("actor") or "system").strip() or "system"

    if membership_request is not None:
        try:
            fields.append({"title": "Request ID", "value": str(membership_request.pk), "short": True})
            fields.append({"title": "Membership type", "value": str(membership_request.membership_type_id), "short": True})
            if org_targeted:
                fields.append(
                    {
                        "title": "Organization",
                        "value": str(kwargs.get("organization_display_name") or membership_request.organization_display_name),
                        "short": True,
                    }
                )
            else:
                fields.append({"title": "Username", "value": str(membership_request.requested_username), "short": True})
        except Exception:
            fields.append({"title": "Membership request", "value": str(membership_request), "short": False})
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
    actor = str(kwargs.get("actor") or "system").strip() or "system"
    organization = kwargs.get("organization")
    if organization is not None:
        try:
            fields.append({"title": "Organization", "value": str(organization.name), "short": True})
            fields.append({"title": "Organization ID", "value": str(organization.pk), "short": True})
        except Exception:
            fields.append({"title": "Organization", "value": str(organization), "short": True})
    fields.append({"title": "Actor", "value": actor, "short": True})
    return fields


def _default_payload(event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    link = _event_link(event_key, kwargs)
    title = _event_title(event_key)
    color = _attachment_color_for_event(event_key)

    if event_key == "account_invitation_accepted":
        text = "Account invitation accepted"
        actor = str(kwargs.get("actor") or "system").strip() or "system"
        fields: list[dict[str, object]] = []
        account_invitation = kwargs.get("account_invitation")
        if account_invitation is not None:
            try:
                fields.append({"title": "Invitation ID", "value": str(account_invitation.pk), "short": True})
            except Exception:
                pass
            try:
                invitation_email = str(account_invitation.email or "").strip()
            except Exception:
                invitation_email = ""
            if invitation_email:
                fields.append({"title": "Email", "value": invitation_email, "short": True})
                title = f"Account invitation {invitation_email}"
            try:
                accepted_username = str(account_invitation.accepted_username or "").strip()
            except Exception:
                accepted_username = ""
            if accepted_username:
                fields.append({"title": "Accepted username", "value": accepted_username, "short": True})
            try:
                matched_usernames = list(account_invitation.freeipa_matched_usernames or [])
            except Exception:
                matched_usernames = []
            if matched_usernames:
                fields.append({"title": "Matched usernames", "value": ", ".join(map(str, matched_usernames)), "short": False})
            try:
                organization = account_invitation.organization
            except Exception:
                organization = None
            if organization is not None:
                try:
                    organization_name = str(organization.name or "").strip()
                except Exception:
                    organization_name = ""
                if organization_name:
                    fields.append({"title": "Organization", "value": organization_name, "short": True})
        fields.append({"title": "Actor", "value": actor, "short": True})
    elif event_key == "election_opened":
        text = "Election opened"
        fields = _election_fields(kwargs)
    elif event_key == "election_closed":
        text = "Election closed"
        fields = _election_fields(kwargs)
    elif event_key == "election_tallied":
        text = "Election tallied"
        fields = _election_fields(kwargs)
    elif event_key == "election_deadline_extended":
        text = "Election deadline extended"
        fields = _election_fields(kwargs)
    elif event_key == "election_quorum_met":
        text = "Election quorum reached"
        fields = _election_fields(kwargs)
    elif event_key == "membership_request_submitted":
        text = "Membership request submitted"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_request_approved":
        text = "Membership request approved"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_request_rejected":
        text = "Membership request rejected"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_request_rescinded":
        text = "Membership request rescinded"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_rfi_sent":
        text = "Membership request RFI sent"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_rfi_replied":
        text = "Membership request RFI replied"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_expiring_soon":
        text = "Memberships expiring soon"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_expired":
        text = "Memberships expired"
        fields = _membership_fields(kwargs, org_targeted=False)
    elif event_key == "membership_self_terminated":
        text = "Membership self-terminated"
        actor = str(kwargs.get("actor") or "system").strip() or "system"
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
        fields.append({"title": "Actor", "value": actor, "short": True})
    elif event_key in _ACCOUNT_DELETION_EVENTS:
        text = _ACCOUNT_DELETION_EVENT_TEXT.get(event_key, _event_title(event_key))
        actor = str(kwargs.get("actor") or "system").strip() or "system"
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
    elif event_key == "organization_membership_request_submitted":
        text = "Organization membership request submitted"
        fields = _membership_fields(kwargs, org_targeted=True)
    elif event_key == "organization_membership_request_approved":
        text = "Organization membership request approved"
        fields = _membership_fields(kwargs, org_targeted=True)
    elif event_key == "organization_membership_request_rejected":
        text = "Organization membership request rejected"
        fields = _membership_fields(kwargs, org_targeted=True)
    elif event_key == "organization_membership_request_rescinded":
        text = "Organization membership request rescinded"
        fields = _membership_fields(kwargs, org_targeted=True)
    elif event_key == "organization_membership_rfi_sent":
        text = "Organization membership request RFI sent"
        fields = _membership_fields(kwargs, org_targeted=True)
    elif event_key == "organization_membership_rfi_replied":
        text = "Organization membership request RFI replied"
        fields = _membership_fields(kwargs, org_targeted=True)
    elif event_key == "organization_claimed":
        text = "Organization claimed"
        fields = _organization_fields(kwargs)
    elif event_key == "organization_created":
        text = "Organization created"
        fields = _organization_fields(kwargs)
    elif event_key == "organization_country_changed":
        text = "Organization country changed"
        fields = _organization_fields(kwargs)
        old_country = str(kwargs.get("old_country") or "").strip()
        new_country = str(kwargs.get("new_country") or "").strip()
        if old_country:
            fields.append({"title": "Old country", "value": old_country, "short": True})
        if new_country:
            fields.append({"title": "New country", "value": new_country, "short": True})
    elif event_key == "user_country_changed":
        text = "User country changed"
        actor = str(kwargs.get("actor") or "system").strip() or "system"
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

    if event_key.startswith("election_"):
        try:
            title = f"Election {kwargs.get('election').name}"
        except Exception:
            pass
    elif event_key == "account_invitation_accepted":
        try:
            title = f"Account invitation {kwargs.get('account_invitation').email}"
        except Exception:
            pass
    elif event_key == "membership_self_terminated":
        membership_type_name = str(kwargs.get("membership_type_name") or "").strip()
        if membership_type_name:
            title = f"Membership {membership_type_name}"
    elif event_key in _ACCOUNT_DELETION_EVENTS:
        request_id = str(kwargs.get("account_deletion_request_id") or "").strip()
        if request_id:
            title = f"Deletion request {request_id}"
    elif event_key.startswith("membership_") or event_key.startswith("organization_membership_"):
        try:
            title = f"Membership request {kwargs.get('membership_request').pk}"
        except Exception:
            pass

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


def _membership_request_template_context(kwargs: dict[str, object]) -> dict[str, object]:
    context: dict[str, object] = {}
    membership_request = kwargs.get("membership_request")
    if membership_request is None:
        return context

    try:
        context["membership_request_id"] = membership_request.pk
    except Exception:
        pass

    try:
        membership_type = membership_request.membership_type
    except Exception:
        membership_type = None

    if membership_type is not None:
        try:
            membership_type_code = str(membership_type.code or "").strip()
            if membership_type_code:
                context["membership_type_code"] = membership_type_code
        except Exception:
            pass

        try:
            membership_type_name = str(membership_type.name or "").strip()
            if membership_type_name:
                context["membership_type_name"] = membership_type_name
        except Exception:
            pass

    try:
        target_kind = membership_request.target_kind
        kind_value = target_kind.value
    except Exception:
        try:
            kind_value = str(membership_request.target_kind or "").strip()
        except Exception:
            kind_value = ""
    if kind_value:
        context["membership_target_kind"] = kind_value

    try:
        requested_username = str(membership_request.requested_username or "").strip()
    except Exception:
        requested_username = ""
    if requested_username:
        context["requested_username"] = requested_username

    try:
        requested_organization_id = membership_request.requested_organization_id
    except Exception:
        requested_organization_id = None
    if requested_organization_id is not None:
        context["requested_organization_id"] = requested_organization_id

    try:
        requested_organization_name = str(membership_request.organization_display_name or "").strip()
    except Exception:
        requested_organization_name = ""
    if requested_organization_name:
        context["requested_organization_name"] = requested_organization_name

    return context


def _election_template_context(event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    context: dict[str, object] = {}
    election = kwargs.get("election")
    if election is None:
        return context

    try:
        election_id = election.pk
    except Exception:
        election_id = None

    if election_id is not None:
        context["election_id"] = election_id
        context["election_genesis_hash"] = election_genesis_chain_hash(election_id)

    try:
        election_name = str(election.name or "").strip()
    except Exception:
        election_name = ""
    if election_name:
        context["election_name"] = election_name

    try:
        election_end_datetime = election.end_datetime
    except Exception:
        election_end_datetime = None
    if isinstance(election_end_datetime, datetime.datetime):
        context["election_end_datetime"] = election_end_datetime
        context["election_end_datetime_iso"] = election_end_datetime.isoformat()

    if event_key == "election_closed":
        final_chain_hash: str | None = None
        try:
            latest_chain_hash = Ballot.objects.latest_chain_head_hash_for_election(election=election)
            if latest_chain_hash:
                final_chain_hash = str(latest_chain_hash)
        except Exception:
            final_chain_hash = None

        if not final_chain_hash and "election_genesis_hash" in context:
            final_chain_hash = str(context["election_genesis_hash"])

        if final_chain_hash:
            context["election_final_chain_hash"] = final_chain_hash

    if event_key == "election_tallied":
        try:
            winners = _extract_tallied_winner_usernames(election.tally_result)
        except Exception:
            winners = []

        context["election_winners"] = winners
        context["election_winners_csv"] = ", ".join(winners)

    return context


def _build_template_context(event_key: str, kwargs: dict[str, object]) -> dict[str, object]:
    context = dict(kwargs)
    context.update(_membership_request_template_context(kwargs))
    context.update(_election_template_context(event_key, kwargs))

    account_invitation = kwargs.get("account_invitation")
    if account_invitation is not None:
        try:
            context["account_invitation_id"] = account_invitation.pk
        except Exception:
            pass

        try:
            invitation_email = str(account_invitation.email or "").strip()
        except Exception:
            invitation_email = ""
        if invitation_email:
            context["account_invitation_email"] = invitation_email

        try:
            invitation_full_name = str(account_invitation.full_name or "").strip()
        except Exception:
            invitation_full_name = ""
        if invitation_full_name:
            context["account_invitation_full_name"] = invitation_full_name

        try:
            accepted_username = str(account_invitation.accepted_username or "").strip()
        except Exception:
            accepted_username = ""
        if accepted_username:
            context["account_invitation_accepted_username"] = accepted_username

        try:
            accepted_at = account_invitation.accepted_at
        except Exception:
            accepted_at = None
        if isinstance(accepted_at, datetime.datetime):
            context["account_invitation_accepted_at"] = accepted_at
            context["account_invitation_accepted_at_iso"] = accepted_at.isoformat()

        try:
            matched_usernames = list(account_invitation.freeipa_matched_usernames or [])
        except Exception:
            matched_usernames = []
        if matched_usernames:
            context["account_invitation_matched_usernames"] = matched_usernames
            context["account_invitation_matched_usernames_csv"] = ", ".join(map(str, matched_usernames))

        try:
            organization_id = account_invitation.organization_id
        except Exception:
            organization_id = None
        if organization_id is not None:
            context["account_invitation_organization_id"] = organization_id

        try:
            organization = account_invitation.organization
        except Exception:
            organization = None
        if organization is not None:
            try:
                organization_name = str(organization.name or "").strip()
            except Exception:
                organization_name = ""
            if organization_name:
                context["account_invitation_organization_name"] = organization_name

    membership_type = kwargs.get("membership_type")
    if membership_type is not None:
        try:
            membership_type_code = str(membership_type.code or "").strip()
        except Exception:
            membership_type_code = ""
        if membership_type_code:
            context["membership_type_code"] = membership_type_code

        try:
            membership_type_name = str(membership_type.name or "").strip()
        except Exception:
            membership_type_name = ""
        if membership_type_name:
            context["membership_type_name"] = membership_type_name

    account_deletion_request = kwargs.get("account_deletion_request")
    if account_deletion_request is not None:
        try:
            context["account_deletion_request_id"] = account_deletion_request.pk
        except Exception:
            pass

        try:
            deletion_username = str(account_deletion_request.username or "").strip()
        except Exception:
            deletion_username = ""
        if deletion_username:
            context["account_deletion_username"] = deletion_username

        try:
            deletion_status = str(account_deletion_request.status or "").strip()
        except Exception:
            deletion_status = ""
        if deletion_status:
            context["account_deletion_status"] = deletion_status

        try:
            manual_review_required = bool(account_deletion_request.manual_review_required)
        except Exception:
            manual_review_required = False
        context["account_deletion_manual_review_required"] = manual_review_required

        try:
            blocker_codes = list(account_deletion_request.blocker_codes or [])
        except Exception:
            blocker_codes = []
        context["account_deletion_blocker_codes"] = blocker_codes
        context["account_deletion_blocker_codes_csv"] = ", ".join(map(str, blocker_codes))

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
    return _apply_endpoint_overrides(
        endpoint=endpoint,
        base_payload=_default_payload(event_key, template_context),
        context=template_context,
    )


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
    configured_icon = str(settings.MATTERMOST_WEBHOOK_DEFAULT_ICON_URL or "").strip()
    if not configured_icon:
        return None
    if configured_icon.startswith("http://") or configured_icon.startswith("https://"):
        return configured_icon
    return build_public_absolute_url(configured_icon, on_missing="relative")


def _apply_default_identity(payload: dict[str, object]) -> dict[str, object]:
    outbound_payload = dict(payload)

    default_username = str(settings.MATTERMOST_WEBHOOK_DEFAULT_USERNAME or "").strip()
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


def dispatch_mattermost_event(event_key: str, **kwargs: object) -> None:
    try:
        if event_key not in CANONICAL_SIGNALS:
            logger.warning("mattermost.unknown_event_key", extra={"event_key": event_key})
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


@connect_once
def connect_mattermost_receivers() -> None:
    for event_key, signal in CANONICAL_SIGNALS.items():
        @safe_receiver(event_key)
        def _receiver(*args: object, __event_key: str = event_key, **kwargs: object) -> None:
            _ = args
            dispatch_mattermost_event(__event_key, **kwargs)

        _RECEIVER_FUNCTIONS[event_key] = _receiver
        signal.connect(_receiver, dispatch_uid=f"core.mattermost_webhooks.{event_key}")
