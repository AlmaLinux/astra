import datetime
import json
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from post_office.models import Email, EmailTemplate

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client, is_e2e_fake_freeipa_enabled
from core.models import AccountInvitation, AccountInvitationSend, FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.rate_limit import clear_subject_rate_limit

INVITATION_OPERATOR_USERNAME = "regular01"
INVITATION_OPERATOR_PASSWORD = "password"
PENDING_EMAIL_DOMAIN = "membership-invitations.invalid"

INVITATION_ALIAS_MATRIX: tuple[dict[str, object], ...] = (
    {
        "alias": "pending_shell_observer",
        "scope": "pending",
        "email": f"wave3+pending-shell-observer@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Shell Observer",
        "note": "Observer row reserved for shell assertions.",
        "organization_name": "Wave 7 Pending Invitation Org",
        "destructive": False,
        "offset_minutes": 1,
    },
    {
        "alias": "accepted_shell_observer",
        "scope": "accepted",
        "email": "regular02@example.test",
        "full_name": "Accepted Shell Observer",
        "note": "Accepted observer row reserved for shell assertions.",
        "organization_name": "Wave 7 Accepted Invitation Org",
        "destructive": False,
        "accepted_username": "regular02",
        "freeipa_matched_usernames": ["regular02"],
        "offset_minutes": 2,
    },
    {
        "alias": "pending_refresh_acceptance",
        "scope": "pending",
        "email": "regular06@example.test",
        "full_name": "Pending Refresh Acceptance",
        "note": "Pending row reserved for refresh-driven acceptance coverage.",
        "destructive": True,
        "offset_minutes": 3,
    },
    {
        "alias": "pending_row_resend",
        "scope": "pending",
        "email": f"wave3+pending-row-resend@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Row Resend",
        "note": "Pending row reserved for single-row resend.",
        "destructive": True,
        "offset_minutes": 4,
    },
    {
        "alias": "pending_row_dismiss",
        "scope": "pending",
        "email": f"wave3+pending-row-dismiss@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Row Dismiss",
        "note": "Pending row reserved for single-row dismiss.",
        "destructive": True,
        "offset_minutes": 5,
    },
    {
        "alias": "pending_bulk_resend_primary",
        "scope": "pending",
        "email": f"wave3+pending-bulk-resend-primary@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Bulk Resend Primary",
        "note": "Primary pending row reserved for bulk resend.",
        "destructive": True,
        "offset_minutes": 6,
    },
    {
        "alias": "pending_bulk_resend_secondary",
        "scope": "pending",
        "email": f"wave3+pending-bulk-resend-secondary@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Bulk Resend Secondary",
        "note": "Secondary pending row reserved for bulk resend.",
        "destructive": True,
        "offset_minutes": 7,
    },
    {
        "alias": "pending_bulk_resend_extra",
        "scope": "pending",
        "email": f"wave3+pending-bulk-resend-extra@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Bulk Resend Extra",
        "note": "Extra pending row reserved for partial refresh assertions.",
        "destructive": True,
        "offset_minutes": 8,
    },
    {
        "alias": "pending_bulk_dismiss_primary",
        "scope": "pending",
        "email": f"wave3+pending-bulk-dismiss-primary@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Bulk Dismiss Primary",
        "note": "Primary pending row reserved for bulk dismiss.",
        "destructive": True,
        "offset_minutes": 9,
    },
    {
        "alias": "pending_bulk_dismiss_secondary",
        "scope": "pending",
        "email": f"wave3+pending-bulk-dismiss-secondary@{PENDING_EMAIL_DOMAIN}",
        "full_name": "Pending Bulk Dismiss Secondary",
        "note": "Secondary pending row reserved for bulk dismiss.",
        "destructive": True,
        "offset_minutes": 10,
    },
    {
        "alias": "accepted_bulk_dismiss_primary",
        "scope": "accepted",
        "email": "regular03@example.test",
        "full_name": "Accepted Bulk Dismiss Primary",
        "note": "Primary accepted row reserved for bulk dismiss.",
        "destructive": True,
        "accepted_username": "regular03",
        "freeipa_matched_usernames": ["regular03"],
        "offset_minutes": 11,
    },
    {
        "alias": "accepted_bulk_dismiss_secondary",
        "scope": "accepted",
        "email": "regular04@example.test",
        "full_name": "Accepted Bulk Dismiss Secondary",
        "note": "Secondary accepted row reserved for bulk dismiss.",
        "destructive": True,
        "accepted_username": "regular04",
        "freeipa_matched_usernames": ["regular04"],
        "offset_minutes": 12,
    },
    {
        "alias": "accepted_bulk_dismiss_extra",
        "scope": "accepted",
        "email": "regular05@example.test",
        "full_name": "Accepted Bulk Dismiss Extra",
        "note": "Extra accepted row reserved for partial refresh assertions.",
        "destructive": True,
        "accepted_username": "regular05",
        "freeipa_matched_usernames": ["regular05"],
        "offset_minutes": 13,
    },
    {
        "alias": "accepted_single_dismiss",
        "scope": "accepted",
        "email": "regular07@example.test",
        "full_name": "Accepted Single Dismiss",
        "note": "Accepted row reserved for single-row dismiss.",
        "destructive": True,
        "accepted_username": "regular07",
        "freeipa_matched_usernames": ["regular07"],
        "offset_minutes": 14,
    },
    {
        "alias": "accepted_multi_match_inspection",
        "scope": "accepted",
        "email": "regular06+multimatch@example.test",
        "full_name": "Accepted Multi Match Inspection",
        "note": "Accepted row reserved for multi-match inspection details.",
        "destructive": False,
        "accepted_username": "regular06",
        "freeipa_matched_usernames": ["regular06", "regular16"],
        "offset_minutes": 15,
    },
)

PENDING_PAGINATION_FILLERS: tuple[dict[str, object], ...] = tuple(
    {
        "alias": f"pending_page_two_filler_{index:02d}",
        "scope": "pending",
        "email": f"wave3+pending-page-two-filler-{index:02d}@{PENDING_EMAIL_DOMAIN}",
        "full_name": f"Pending Pagination Filler {index:02d}",
        "note": "Pending filler row reserved for invitation pagination coverage.",
        "destructive": False,
        "offset_minutes": 30 + index,
    }
    for index in range(1, 21)
)

ACCEPTED_PAGINATION_FILLERS: tuple[dict[str, object], ...] = tuple(
    {
        "alias": f"accepted_page_two_filler_{index:02d}",
        "scope": "accepted",
        "email": f"wave3+accepted-page-two-filler-{index:02d}@accepted-membership-invitations.invalid",
        "full_name": f"Accepted Pagination Filler {index:02d}",
        "note": "Accepted filler row reserved for invitation pagination coverage.",
        "destructive": False,
        "accepted_username": f"accepted-filler-{index:02d}",
        "freeipa_matched_usernames": [f"accepted-filler-{index:02d}"],
        "offset_minutes": 60 + index,
    }
    for index in range(1, 23)
)

ALL_INVITATION_DEFINITIONS: tuple[dict[str, object], ...] = (
    *INVITATION_ALIAS_MATRIX,
    *PENDING_PAGINATION_FILLERS,
    *ACCEPTED_PAGINATION_FILLERS,
)

ORGANIZATION_LINK_NAMES: tuple[str, ...] = tuple(
    str(definition["organization_name"])
    for definition in INVITATION_ALIAS_MATRIX
    if "organization_name" in definition
)

SCENARIO_ALIAS_MATRIX: dict[str, dict[str, object]] = {
    "invitations-list-shell": {
        "aliases": ["pending_shell_observer", "accepted_shell_observer"],
        "destructive": False,
    },
    "invitations-pending-row-actions": {
        "aliases": ["pending_row_resend", "pending_row_dismiss"],
        "destructive": True,
    },
    "invitations-refresh-now": {
        "aliases": ["pending_refresh_acceptance"],
        "destructive": True,
    },
    "invitations-pending-bulk-resend": {
        "aliases": [
            "pending_bulk_resend_primary",
            "pending_bulk_resend_secondary",
            "pending_bulk_resend_extra",
        ],
        "destructive": True,
    },
    "invitations-pending-bulk-dismiss": {
        "aliases": ["pending_bulk_dismiss_primary", "pending_bulk_dismiss_secondary"],
        "destructive": True,
    },
    "invitations-accepted-bulk-dismiss": {
        "aliases": [
            "accepted_bulk_dismiss_primary",
            "accepted_bulk_dismiss_secondary",
            "accepted_bulk_dismiss_extra",
        ],
        "destructive": True,
    },
    "invitations-accepted-single-dismiss": {
        "aliases": ["accepted_single_dismiss"],
        "destructive": True,
    },
    "invitations-accepted-inspection": {
        "aliases": ["accepted_multi_match_inspection"],
        "destructive": False,
    },
}


class Command(BaseCommand):
    help = "Reset the Wave 3 account invitations E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "account_invitations_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        with transaction.atomic():
            template_name = self._require_template_prerequisite()
            self._ensure_permission_grants()
            self._ensure_invitation_operator()
            self._clear_existing_slice()
            payload = self._seed_slice(template_name=template_name)

        self.stdout.write(json.dumps(payload))

    def _require_template_prerequisite(self) -> str:
        template_name = str(settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME).strip()
        if not EmailTemplate.objects.filter(name=template_name).exists():
            raise CommandError(
                f"account_invitations_reset requires the configured invitation template {template_name!r}."
            )
        return template_name

    def _ensure_permission_grants(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _ensure_invitation_operator(self) -> None:
        client = get_e2e_service_client()
        client.group_add(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, o_description="Fake E2E membership committee group")
        client.user_mod(
            INVITATION_OPERATOR_USERNAME,
            memberof_group=["packagers", settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            c="US",
        )

        agreement_cn = settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN
        agreement = FreeIPAFASAgreement.get(agreement_cn)
        if agreement is None:
            agreement = FreeIPAFASAgreement.create(agreement_cn, description="CoC")
        if INVITATION_OPERATOR_USERNAME not in agreement.users:
            agreement.add_user(INVITATION_OPERATOR_USERNAME)

    def _clear_existing_slice(self) -> None:
        slice_emails = [str(definition["email"]) for definition in ALL_INVITATION_DEFINITIONS]
        invitations = list(AccountInvitation.objects.filter(email__in=slice_emails).order_by("pk"))
        linked_email_ids = [
            invitation_send.post_office_email_id
            for invitation_send in AccountInvitationSend.objects.filter(invitation__in=invitations)
            if invitation_send.post_office_email_id is not None
        ]

        for invitation in invitations:
            clear_subject_rate_limit(scope="account_invitation_resend", subject=str(invitation.pk))

        clear_subject_rate_limit(scope="account_invitation_bulk_resend", subject=INVITATION_OPERATOR_USERNAME)

        if linked_email_ids:
            Email.objects.filter(pk__in=linked_email_ids).delete()

        AccountInvitation.objects.filter(pk__in=[invitation.pk for invitation in invitations]).delete()
        if ORGANIZATION_LINK_NAMES:
            Organization.objects.filter(name__in=ORGANIZATION_LINK_NAMES).delete()

    def _seed_slice(self, *, template_name: str) -> dict[str, object]:
        alias_payloads: dict[str, dict[str, object]] = {}
        alias_invitation_ids: dict[str, int] = {}
        tracked_aliases = {str(definition["alias"]) for definition in INVITATION_ALIAS_MATRIX}

        for definition in ALL_INVITATION_DEFINITIONS:
            invitation = self._create_invitation(definition=definition, template_name=template_name)
            alias = str(definition["alias"])
            if alias in tracked_aliases:
                alias_invitation_ids[alias] = invitation.pk
                alias_payloads[alias] = self._invitation_payload(invitation=invitation, definition=definition)

        return {
            "scenario": "membership-invitations",
            "status": "reset",
            "actors": {
                "invitation_operator": {
                    "username": INVITATION_OPERATOR_USERNAME,
                    "password": INVITATION_OPERATOR_PASSWORD,
                    "invitation_aliases": alias_payloads,
                }
            },
            "scenarios": {
                scenario_name: {
                    "actor": INVITATION_OPERATOR_USERNAME,
                    "aliases": list(definition["aliases"]),
                    "destructive": bool(definition["destructive"]),
                }
                for scenario_name, definition in SCENARIO_ALIAS_MATRIX.items()
            },
            "invitations": alias_invitation_ids,
        }

    def _create_invitation(self, *, definition: dict[str, object], template_name: str) -> AccountInvitation:
        accepted_username = str(definition.get("accepted_username", ""))
        is_accepted = bool(accepted_username)
        now = timezone.now()
        event_time = now - datetime.timedelta(minutes=int(definition["offset_minutes"]))
        organization = self._create_linked_organization(definition=definition)
        invitation = AccountInvitation.objects.create(
            email=str(definition["email"]),
            full_name=str(definition["full_name"]),
            note=str(definition["note"]),
            organization=organization,
            invited_by_username=INVITATION_OPERATOR_USERNAME,
            email_template_name=template_name,
            send_count=0,
            accepted_username=accepted_username,
            accepted_at=event_time if is_accepted else None,
            freeipa_matched_usernames=list(definition.get("freeipa_matched_usernames", [])),
            freeipa_last_checked_at=event_time if is_accepted else None,
        )
        AccountInvitation.objects.filter(pk=invitation.pk).update(invited_at=event_time)
        invitation.refresh_from_db()
        return invitation

    def _create_linked_organization(self, *, definition: dict[str, object]) -> Organization | None:
        if "organization_name" not in definition:
            return None

        organization_name = str(definition["organization_name"])
        organization_slug = organization_name.lower().replace(" ", "-")
        return Organization.objects.create(
            name=organization_name,
            representative="",
            website=f"https://{organization_slug}.example.test",
            business_contact_name=f"{organization_name} Contact",
            business_contact_email=f"{organization_slug}@example.test",
            country_code="US",
        )

    def _invitation_payload(
        self,
        *,
        invitation: AccountInvitation,
        definition: dict[str, object],
    ) -> dict[str, object]:
        is_accepted = invitation.accepted_at is not None
        resend_eligibility = "accepted_preseeded" if is_accepted else "pending_non_matching"
        if str(definition["alias"]) == "pending_refresh_acceptance":
            resend_eligibility = "pending_refresh_match"
        return {
            "invitation_id": invitation.pk,
            "alias": str(definition["alias"]),
            "scope": str(definition["scope"]),
            "email": invitation.email,
            "acceptance_state": "accepted" if is_accepted else "pending",
            "resend_eligibility": resend_eligibility,
            "accepted_username": invitation.accepted_username,
            "freeipa_matched_usernames": invitation.freeipa_matched_usernames,
            "destructive": bool(definition["destructive"]),
            "email_template_name": invitation.email_template_name,
            "organization_id": invitation.organization_id,
            "organization_name": invitation.organization.name if invitation.organization_id is not None else "",
        }