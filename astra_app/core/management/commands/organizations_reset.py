import datetime
import json
from typing import Final, override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client, is_e2e_fake_freeipa_enabled
from core.models import (
    FreeIPAPermissionGrant,
    Membership,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.organization_claim import make_organization_claim_token
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP

REPRESENTATIVE_OBSERVER_USERNAME: Final[str] = "regular11"
CLAIM_HAPPY_USERNAME: Final[str] = "regular12"
CLAIM_REJECTION_USERNAME: Final[str] = "regular13"
NO_ORG_USERNAME: Final[str] = "regular14"
CLAIMED_OWNER_USERNAME: Final[str] = "wave4-claimed-owner"
ACTOR_PASSWORD: Final[str] = "password"

DETAIL_FOCUS_ALIAS: Final[str] = "detail_focus_org"
MY_ORG_ALIAS: Final[str] = "my_org"
SPONSOR_SHELL_ALIAS: Final[str] = "sponsor_shell_observer"
SPONSOR_SEARCH_ALIAS: Final[str] = "sponsor_search_hit"
MIRROR_SHELL_ALIAS: Final[str] = "mirror_shell_observer"
CLAIMABLE_ALIAS: Final[str] = "claimable_org"
ALREADY_CLAIMED_ALIAS: Final[str] = "already_claimed_org"
DETAIL_PENDING_REQUEST_ALIAS: Final[str] = "detail_pending_request"
SPONSOR_PAGE_TWO_ALIAS: Final[str] = "sponsor_page_two_org"
MIRROR_PAGE_TWO_ALIAS: Final[str] = "mirror_page_two_org"

SPONSOR_PAGINATION_FILLERS: Final[tuple[dict[str, object], ...]] = tuple(
    {
        "alias": f"sponsor_pagination_{index:02d}",
        "name": f"Wave 4 Sponsor Pagination {index:02d}",
        "representative": f"wave4-sponsor-pagination-owner-{index:02d}",
        "website": f"https://wave4-sponsor-pagination-{index:02d}.example.test",
        "business_contact_email": f"sponsor-pagination-{index:02d}@example.test",
        "memberships": ["gold"],
    }
    for index in range(1, 25)
)

MIRROR_PAGINATION_FILLERS: Final[tuple[dict[str, object], ...]] = tuple(
    {
        "alias": f"mirror_pagination_{index:02d}",
        "name": f"Wave 4 Mirror Pagination {index:02d}",
        "representative": f"wave4-mirror-pagination-owner-{index:02d}",
        "website": f"https://wave4-mirror-pagination-{index:02d}.example.test",
        "business_contact_email": f"mirror-pagination-{index:02d}@example.test",
        "memberships": ["mirror"],
    }
    for index in range(1, 25)
)

ORGANIZATION_DEFINITIONS: Final[tuple[dict[str, object], ...]] = (
    {
        "alias": MY_ORG_ALIAS,
        "name": "Wave 4 My Organization",
        "representative": REPRESENTATIVE_OBSERVER_USERNAME,
        "website": "https://wave4-my-org.example.test",
        "business_contact_name": "Wave 4 Business Contact",
        "business_contact_email": "observer@wave4-my-org.example.test",
        "pr_marketing_contact_name": "Wave 4 Marketing Contact",
        "pr_marketing_contact_email": "marketing@wave4-my-org.example.test",
        "technical_contact_name": "Wave 4 Technical Contact",
        "technical_contact_email": "tech@wave4-my-org.example.test",
        "memberships": ["gold"],
    },
    {
        "alias": SPONSOR_SHELL_ALIAS,
        "name": "Wave 4 00 Sponsor Shell Observer",
        "representative": "wave4-sponsor-shell-owner",
        "website": "https://wave4-sponsor-shell.example.test",
        "business_contact_email": "sponsor-shell@example.test",
        "memberships": ["gold"],
    },
    {
        "alias": SPONSOR_SEARCH_ALIAS,
        "name": "Wave 4 00 Sponsor Search Hit",
        "representative": "wave4-sponsor-search-owner",
        "website": "https://wave4-sponsor-search.example.test",
        "business_contact_email": "sponsor-search@example.test",
        "memberships": ["gold"],
    },
    {
        "alias": MIRROR_SHELL_ALIAS,
        "name": "Wave 4 00 Mirror Shell Observer",
        "representative": "wave4-mirror-owner",
        "website": "https://wave4-mirror.example.test",
        "business_contact_email": "mirror-shell@example.test",
        "memberships": ["mirror"],
    },
    {
        "alias": CLAIMABLE_ALIAS,
        "name": "Wave 4 Claimable Org",
        "representative": "",
        "website": "https://wave4-claimable.example.test",
        "business_contact_email": "claimable@example.test",
        "memberships": [],
    },
    {
        "alias": ALREADY_CLAIMED_ALIAS,
        "name": "Wave 4 Already Claimed Org",
        "representative": CLAIMED_OWNER_USERNAME,
        "website": "https://wave4-already-claimed.example.test",
        "business_contact_email": "already-claimed@example.test",
        "memberships": ["gold"],
    },
    *SPONSOR_PAGINATION_FILLERS,
    {
        "alias": SPONSOR_PAGE_TWO_ALIAS,
        "name": "Wave 4 ZZ Sponsor Page Two",
        "representative": "wave4-sponsor-page-two-owner",
        "website": "https://wave4-sponsor-page-two.example.test",
        "business_contact_email": "sponsor-page-two@example.test",
        "memberships": ["gold"],
    },
    *MIRROR_PAGINATION_FILLERS,
    {
        "alias": MIRROR_PAGE_TWO_ALIAS,
        "name": "Wave 4 ZZ Mirror Page Two",
        "representative": "wave4-mirror-page-two-owner",
        "website": "https://wave4-mirror-page-two.example.test",
        "business_contact_email": "mirror-page-two@example.test",
        "memberships": ["mirror"],
    },
)

SCENARIO_ALIAS_MATRIX: Final[dict[str, dict[str, object]]] = {
    "organizations-list-shell": {
        "actor": REPRESENTATIVE_OBSERVER_USERNAME,
        "aliases": [MY_ORG_ALIAS, SPONSOR_SHELL_ALIAS, MIRROR_SHELL_ALIAS],
        "destructive": False,
        "route_target_alias": MY_ORG_ALIAS,
    },
    "organizations-sponsor-search-mirror-stability": {
        "actor": REPRESENTATIVE_OBSERVER_USERNAME,
        "aliases": [SPONSOR_SEARCH_ALIAS, MIRROR_SHELL_ALIAS],
        "destructive": False,
        "route_target_alias": MY_ORG_ALIAS,
    },
    "organizations-detail-membership-state": {
        "actor": REPRESENTATIVE_OBSERVER_USERNAME,
        "aliases": [DETAIL_FOCUS_ALIAS, DETAIL_PENDING_REQUEST_ALIAS],
        "destructive": False,
        "route_target_alias": DETAIL_FOCUS_ALIAS,
    },
    "organizations-claim-happy-path": {
        "actor": CLAIM_HAPPY_USERNAME,
        "aliases": [CLAIMABLE_ALIAS],
        "destructive": True,
        "route_target_alias": CLAIMABLE_ALIAS,
    },
    "organizations-claim-already-claimed": {
        "actor": CLAIM_REJECTION_USERNAME,
        "aliases": [ALREADY_CLAIMED_ALIAS],
        "destructive": False,
        "route_target_alias": ALREADY_CLAIMED_ALIAS,
    },
    "organizations-list-pagination-and-create-cta": {
        "actor": NO_ORG_USERNAME,
        "aliases": [SPONSOR_PAGE_TWO_ALIAS, MIRROR_PAGE_TWO_ALIAS],
        "destructive": False,
        "route_target_alias": SPONSOR_PAGE_TWO_ALIAS,
    },
}


class Command(BaseCommand):
    help = "Reset the Wave 4 organizations E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "organizations_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        with transaction.atomic():
            self._ensure_membership_types()
            self._clear_existing_slice()
            self._ensure_actor_prerequisites()
            payload = self._seed_slice()

        self.stdout.write(json.dumps(payload))

    def _ensure_membership_types(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 1,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
                "group_cn": "almalinux-mirror",
                "description": "Wave 4 mirror membership",
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
                "description": "Wave 4 gold sponsorship",
            },
        )
        MembershipType.objects.update_or_create(
            code="ruby",
            defaults={
                "name": "Ruby Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
                "group_cn": "almalinux-ruby",
                "description": "Wave 4 ruby sponsorship",
            },
        )

    def _clear_existing_slice(self) -> None:
        organization_names = [str(definition["name"]) for definition in ORGANIZATION_DEFINITIONS]
        representative_usernames = [
            representative
            for representative in {
                str(definition["representative"])
                for definition in ORGANIZATION_DEFINITIONS
                if str(definition["representative"]).strip()
            }
        ]
        organizations = list(
            Organization.objects.filter(
                Q(name__in=organization_names) | Q(representative__in=representative_usernames)
            ).order_by("pk")
        )
        organization_ids = [organization.pk for organization in organizations]

        if organization_ids:
            MembershipRequest.objects.filter(requested_organization_id__in=organization_ids).delete()
            Membership.objects.filter(target_organization_id__in=organization_ids).delete()
            Organization.objects.filter(pk__in=organization_ids).delete()

    def _ensure_actor_prerequisites(self) -> None:
        client = get_e2e_service_client()
        country_attr = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR).strip()
        for group_cn in ["almalinux-gold", "almalinux-mirror", "almalinux-ruby"]:
            client.group_add(group_cn, o_description=f"Wave 4 E2E {group_cn} group")

        agreement_cn = settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN
        agreement = FreeIPAFASAgreement.get(agreement_cn)
        if agreement is None:
            agreement = FreeIPAFASAgreement.create(agreement_cn, description="CoC")

        for username in [REPRESENTATIVE_OBSERVER_USERNAME, CLAIM_HAPPY_USERNAME, CLAIM_REJECTION_USERNAME, NO_ORG_USERNAME]:
            client.user_mod(username, **{country_attr: "US"})
            if username not in agreement.users:
                agreement.add_user(username)

        for permission in (ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.update_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=REPRESENTATIVE_OBSERVER_USERNAME,
            )

    def _seed_slice(self) -> dict[str, object]:
        organizations_by_alias: dict[str, Organization] = {}
        organizations_payload: dict[str, dict[str, object]] = {}

        for definition in ORGANIZATION_DEFINITIONS:
            organization = self._create_organization(definition=definition)
            alias = str(definition["alias"])
            organizations_by_alias[alias] = organization
            organizations_payload[alias] = self._organization_payload(organization)

        organizations_by_alias[DETAIL_FOCUS_ALIAS] = organizations_by_alias[MY_ORG_ALIAS]
        organizations_payload[DETAIL_FOCUS_ALIAS] = dict(organizations_payload[MY_ORG_ALIAS])

        detail_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organizations_by_alias[MY_ORG_ALIAS],
            membership_type_id="mirror",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=5),
            responses=[{"Domain": "https://wave4-detail-on-hold.example.test"}],
        )
        MembershipRequest.objects.filter(pk=detail_request.pk).update(
            requested_at=timezone.now() - datetime.timedelta(days=10)
        )
        detail_request.refresh_from_db()

        claim_routes = {
            "organizations-claim-happy-path": reverse(
                "organization-claim",
                args=[make_organization_claim_token(organizations_by_alias[CLAIMABLE_ALIAS])],
            ),
            "organizations-claim-already-claimed": reverse(
                "organization-claim",
                args=[make_organization_claim_token(organizations_by_alias[ALREADY_CLAIMED_ALIAS])],
            ),
        }
        requests_payload = {
            DETAIL_PENDING_REQUEST_ALIAS: {
                "request_id": detail_request.pk,
                "status": detail_request.status,
                "detail_url": reverse("membership-request-detail", args=[detail_request.pk]),
                "organization_id": organizations_by_alias[MY_ORG_ALIAS].pk,
            }
        }

        scenarios_payload: dict[str, dict[str, object]] = {}
        for scenario_name, definition in SCENARIO_ALIAS_MATRIX.items():
            route_target_alias = str(definition["route_target_alias"])
            scenarios_payload[scenario_name] = {
                "actor": str(definition["actor"]),
                "aliases": list(definition["aliases"]),
                "destructive": bool(definition["destructive"]),
                "route_target": organizations_payload[route_target_alias]["detail_url"],
            }

        return {
            "scenario": "organizations",
            "status": "reset",
            "actors": {
                "representative_observer": {
                    "username": REPRESENTATIVE_OBSERVER_USERNAME,
                    "password": ACTOR_PASSWORD,
                    "organization_aliases": {
                        MY_ORG_ALIAS: organizations_by_alias[MY_ORG_ALIAS].pk,
                        DETAIL_FOCUS_ALIAS: organizations_by_alias[MY_ORG_ALIAS].pk,
                        SPONSOR_SHELL_ALIAS: organizations_by_alias[SPONSOR_SHELL_ALIAS].pk,
                        SPONSOR_SEARCH_ALIAS: organizations_by_alias[SPONSOR_SEARCH_ALIAS].pk,
                        MIRROR_SHELL_ALIAS: organizations_by_alias[MIRROR_SHELL_ALIAS].pk,
                    },
                    "request_aliases": {
                        DETAIL_PENDING_REQUEST_ALIAS: detail_request.pk,
                    },
                },
                "claim_happy_actor": {
                    "username": CLAIM_HAPPY_USERNAME,
                    "password": ACTOR_PASSWORD,
                    "organization_aliases": {
                        CLAIMABLE_ALIAS: organizations_by_alias[CLAIMABLE_ALIAS].pk,
                    },
                },
                "claim_rejection_actor": {
                    "username": CLAIM_REJECTION_USERNAME,
                    "password": ACTOR_PASSWORD,
                    "organization_aliases": {
                        ALREADY_CLAIMED_ALIAS: organizations_by_alias[ALREADY_CLAIMED_ALIAS].pk,
                    },
                },
                "no_org_actor": {
                    "username": NO_ORG_USERNAME,
                    "password": ACTOR_PASSWORD,
                    "organization_aliases": {},
                },
            },
            "claim_routes": claim_routes,
            "organizations": organizations_payload,
            "requests": requests_payload,
            "scenarios": scenarios_payload,
        }

    def _create_organization(self, *, definition: dict[str, object]) -> Organization:
        organization = Organization.objects.create(
            name=str(definition["name"]),
            representative=str(definition["representative"]),
            website=str(definition["website"]),
            business_contact_email=str(definition["business_contact_email"]),
            business_contact_name=str(definition.get("business_contact_name") or "Wave 4 Contact"),
            pr_marketing_contact_name=str(definition.get("pr_marketing_contact_name") or ""),
            pr_marketing_contact_email=str(definition.get("pr_marketing_contact_email") or ""),
            technical_contact_name=str(definition.get("technical_contact_name") or ""),
            technical_contact_email=str(definition.get("technical_contact_email") or ""),
            country_code="US",
        )
        if definition["alias"] == ALREADY_CLAIMED_ALIAS:
            organization.claim_secret = "wave4-already-claimed-secret"
            organization.save(update_fields=["claim_secret"])
        if definition["alias"] == MY_ORG_ALIAS:
            membership = Membership.objects.create(
                target_organization=organization,
                membership_type_id="gold",
                expires_at=timezone.now() + datetime.timedelta(days=120),
            )
            Membership.objects.filter(pk=membership.pk).update(
                created_at=timezone.now() - datetime.timedelta(days=480)
            )
        else:
            for membership_type_code in definition["memberships"]:
                Membership.objects.create(
                    target_organization=organization,
                    membership_type_id=str(membership_type_code),
                    expires_at=timezone.now() + datetime.timedelta(days=180),
                )
        return organization

    def _organization_payload(self, organization: Organization) -> dict[str, object]:
        return {
            "organization_id": organization.pk,
            "name": organization.name,
            "status": organization.status,
            "detail_url": reverse("organization-detail", args=[organization.pk]),
            "business_contact_email": organization.business_contact_email,
        }