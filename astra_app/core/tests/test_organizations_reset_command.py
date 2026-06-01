import io
import json

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client
from core.models import FreeIPAPermissionGrant, Membership, MembershipRequest, MembershipType, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class OrganizationsResetCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("organizations_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_wave4_organization_scenarios_idempotently_with_fresh_claim_routes(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()

        call_command("organizations_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("organizations_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload["scenario"], "organizations")
        self.assertEqual(second_payload["scenario"], "organizations")
        self.assertEqual(
            set(first_payload["actors"].keys()),
            {"representative_observer", "claim_happy_actor", "claim_rejection_actor", "no_org_actor"},
        )
        self.assertEqual(
            set(first_payload["actors"]["representative_observer"]["organization_aliases"].keys()),
            {
                "my_org",
                "detail_focus_org",
                "sponsor_shell_observer",
                "sponsor_search_hit",
                "mirror_shell_observer",
            },
        )
        self.assertEqual(
            set(first_payload["actors"]["representative_observer"]["request_aliases"].keys()),
            {"detail_pending_request"},
        )
        self.assertEqual(
            set(first_payload["actors"]["claim_happy_actor"]["organization_aliases"].keys()),
            {"claimable_org"},
        )
        self.assertEqual(
            set(first_payload["actors"]["claim_rejection_actor"]["organization_aliases"].keys()),
            {"already_claimed_org"},
        )
        self.assertEqual(first_payload["actors"]["no_org_actor"]["organization_aliases"], {})
        self.assertEqual(
            {actor["username"] for actor in first_payload["actors"].values()},
            {"regular11", "regular12", "regular13", "regular14"},
        )
        self.assertEqual(
            set(first_payload["claim_routes"].keys()),
            {"organizations-claim-happy-path", "organizations-claim-already-claimed"},
        )
        self.assertEqual(
            set(first_payload["scenarios"].keys()),
            {
                "organizations-list-shell",
                "organizations-sponsor-search-mirror-stability",
                "organizations-detail-membership-state",
                "organizations-claim-happy-path",
                "organizations-claim-already-claimed",
                "organizations-list-pagination-and-create-cta",
            },
        )
        self.assertNotEqual(
            first_payload["claim_routes"]["organizations-claim-happy-path"],
            second_payload["claim_routes"]["organizations-claim-happy-path"],
        )
        self.assertNotEqual(
            first_payload["claim_routes"]["organizations-claim-already-claimed"],
            second_payload["claim_routes"]["organizations-claim-already-claimed"],
        )
        self.assertTrue(
            first_payload["claim_routes"]["organizations-claim-happy-path"].startswith("/organizations/claim/")
        )
        self.assertTrue(
            first_payload["claim_routes"]["organizations-claim-already-claimed"].startswith("/organizations/claim/")
        )

        self.assertEqual(
            first_payload["actors"]["representative_observer"]["organization_aliases"]["my_org"],
            first_payload["actors"]["representative_observer"]["organization_aliases"]["detail_focus_org"],
        )

        my_org = Organization.objects.get(
            pk=second_payload["actors"]["representative_observer"]["organization_aliases"]["my_org"]
        )
        claimable_org = Organization.objects.get(
            pk=second_payload["actors"]["claim_happy_actor"]["organization_aliases"]["claimable_org"]
        )
        already_claimed_org = Organization.objects.get(
            pk=second_payload["actors"]["claim_rejection_actor"]["organization_aliases"]["already_claimed_org"]
        )

        self.assertEqual(my_org.representative, "regular11")
        self.assertEqual(claimable_org.representative, "")
        self.assertEqual(already_claimed_org.representative, "wave4-claimed-owner")

    @override_settings(
        ASTRA_E2E_MODE=True,
        ASTRA_E2E_FAKE_FREEIPA_ENABLED=True,
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote",
    )
    def test_command_owns_claim_prerequisites_and_detail_seed_state(self) -> None:
        call_command("organizations_reset")

        client = get_e2e_service_client()
        country_attr = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR
        for username in ["regular11", "regular12", "regular13", "regular14"]:
            user = client.user_show(username)["result"]
            self.assertEqual(user.get(country_attr), ["US"])

        agreement = FreeIPAFASAgreement.get(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)
        self.assertIsNotNone(agreement)
        self.assertIn("regular11", agreement.users)
        self.assertIn("regular12", agreement.users)
        self.assertIn("regular13", agreement.users)
        self.assertIn("regular14", agreement.users)

        self.assertTrue(MembershipType.objects.filter(code="gold", category_id="sponsorship", enabled=True).exists())
        self.assertTrue(MembershipType.objects.filter(code="mirror", category_id="mirror", enabled=True).exists())

        detail_organization = Organization.objects.get(
            representative="regular11",
        )
        self.assertTrue(
            Membership.objects.filter(target_organization=detail_organization, membership_type_id="gold").exists()
        )
        self.assertTrue(
            MembershipRequest.objects.filter(
                requested_organization=detail_organization,
                membership_type_id="mirror",
                status=MembershipRequest.Status.on_hold,
            ).exists()
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_no_org_cta_actor_and_second_pages_for_sponsor_and_mirror_cards(self) -> None:
        stdout = io.StringIO()

        call_command("organizations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        no_org_actor = payload["actors"]["no_org_actor"]
        sponsor_page_two_name = payload["organizations"]["sponsor_page_two_org"]["name"]
        mirror_page_two_name = payload["organizations"]["mirror_page_two_org"]["name"]

        self.assertEqual(no_org_actor["username"], "regular14")
        self.assertEqual(no_org_actor["organization_aliases"], {})

        self.assertTrue(
            Organization.objects.filter(
                pk=payload["organizations"]["sponsor_page_two_org"]["organization_id"],
                representative="wave4-sponsor-page-two-owner",
            ).exists()
        )
        self.assertTrue(
            Organization.objects.filter(
                pk=payload["organizations"]["mirror_page_two_org"]["organization_id"],
                representative="wave4-mirror-page-two-owner",
            ).exists()
        )

        self.assertGreaterEqual(len(payload["organizations"]), 9)
        self.assertIn("organizations-list-pagination-and-create-cta", payload["scenarios"])

        self._login_as_freeipa_user(no_org_actor["username"])
        sponsor_page_two_response = self.client.get(
            "/api/v1/organizations",
            {"page_sponsor": "2"},
            HTTP_ACCEPT="application/json",
        )
        mirror_page_two_response = self.client.get(
            "/api/v1/organizations",
            {"page_mirror": "2"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(sponsor_page_two_response.status_code, 200)
        self.assertEqual(mirror_page_two_response.status_code, 200)

        sponsor_page_two_payload = sponsor_page_two_response.json()["sponsor_card"]
        mirror_page_two_payload = mirror_page_two_response.json()["mirror_card"]

        self.assertEqual(sponsor_page_two_payload["pagination"]["page"], 2)
        self.assertIn(sponsor_page_two_name, [item["name"] for item in sponsor_page_two_payload["items"]])
        self.assertEqual(mirror_page_two_payload["pagination"]["page"], 2)
        self.assertIn(mirror_page_two_name, [item["name"] for item in mirror_page_two_payload["items"]])

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_representative_detail_permissions_for_sponsorship_controls(self) -> None:
        stdout = io.StringIO()

        call_command("organizations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        observer_username = payload["actors"]["representative_observer"]["username"]
        detail_org_id = payload["organizations"]["detail_focus_org"]["organization_id"]

        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_CHANGE_MEMBERSHIP,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=observer_username,
            ).exists()
        )
        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_DELETE_MEMBERSHIP,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=observer_username,
            ).exists()
        )
        self.assertTrue(MembershipType.objects.filter(code="ruby", category_id="sponsorship", enabled=True).exists())

        self._login_as_freeipa_user(observer_username)
        detail_response = self.client.get(
            reverse("api-organization-detail-page", args=[detail_org_id]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()["organization"]
        self.assertTrue(detail_payload["memberships"])
        first_membership = detail_payload["memberships"][0]
        self.assertTrue(first_membership["can_request_tier_change"])
        self.assertEqual(first_membership["tier_change_membership_type_code"], "ruby")
        self.assertTrue(first_membership["can_manage_expiration"])

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_keeps_shell_and_search_aliases_visible_on_first_card_pages(self) -> None:
        stdout = io.StringIO()

        call_command("organizations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        observer_username = payload["actors"]["representative_observer"]["username"]

        self._login_as_freeipa_user(observer_username)
        response = self.client.get(
            reverse("api-organizations"),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        response_payload = response.json()
        sponsor_names = {item["name"] for item in response_payload["sponsor_card"]["items"]}
        mirror_names = {item["name"] for item in response_payload["mirror_card"]["items"]}

        self.assertIn(payload["organizations"]["sponsor_shell_observer"]["name"], sponsor_names)
        self.assertIn(payload["organizations"]["sponsor_search_hit"]["name"], sponsor_names)
        self.assertIn(payload["organizations"]["mirror_shell_observer"]["name"], mirror_names)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_prior_wave4_representative_owned_rows_even_if_names_drift(self) -> None:
        Organization.objects.create(
            name="Legacy Wave 4 Sponsor Shell Drift",
            representative="wave4-sponsor-shell-owner",
            business_contact_email="legacy-sponsor-shell@example.test",
            website="https://legacy-sponsor-shell.example.test",
            country_code="US",
        )
        stdout = io.StringIO()

        call_command("organizations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertFalse(Organization.objects.filter(name="Legacy Wave 4 Sponsor Shell Drift").exists())
        self.assertTrue(
            Organization.objects.filter(
                pk=payload["actors"]["representative_observer"]["organization_aliases"]["sponsor_shell_observer"],
                representative="wave4-sponsor-shell-owner",
            ).exists()
        )