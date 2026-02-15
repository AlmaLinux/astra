from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import Organization
from core.organization_claim import make_organization_claim_token
from core.tests.utils_test_data import ensure_core_categories


class OrganizationClaimFlowTests(TestCase):
    def setUp(self) -> None:
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_creating_org_without_representative_defaults_to_unclaimed_and_sets_claim_secret(self) -> None:
        organization = Organization.objects.create(name="Unclaimed Org")

        self.assertEqual(organization.representative, "")
        self.assertEqual(organization.status, Organization.Status.unclaimed)
        self.assertNotEqual(organization.claim_secret, "")

    def test_claim_page_with_invalid_token_shows_invalid_or_expired_message(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.views_organizations.FreeIPAUser.get", return_value=alice):
            response = self.client.get(reverse("organization-claim", args=["not-a-valid-token"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This claim link is invalid or has expired.")

    def test_successful_claim_sets_representative_marks_active_and_rotates_secret(self) -> None:
        organization = Organization.objects.create(
            name="Claimable Org",
            business_contact_email="contact@claimable.example",
        )
        old_secret = organization.claim_secret
        token = make_organization_claim_token(organization)

        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.views_organizations.block_action_without_coc", return_value=None),
            patch("core.views_organizations.block_action_without_country_code", return_value=None),
            patch("core.views_organizations.FreeIPAUser.get", return_value=alice),
        ):
            get_response = self.client.get(reverse("organization-claim", args=[token]))
            self.assertEqual(get_response.status_code, 200)
            self.assertContains(get_response, "Claim this organization to become its representative.")
            self.assertContains(get_response, "Claimable Org")
            self.assertContains(get_response, "Contact email")
            self.assertContains(get_response, "contact@claimable.example")

            post_response = self.client.post(reverse("organization-claim", args=[token]), follow=True)

        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(post_response.request["PATH_INFO"], reverse("organization-detail", args=[organization.pk]))

        organization.refresh_from_db()
        self.assertEqual(organization.representative, "alice")
        self.assertEqual(organization.status, Organization.Status.active)
        self.assertNotEqual(organization.claim_secret, "")
        self.assertNotEqual(organization.claim_secret, old_secret)

        messages = [message.message for message in get_messages(post_response.wsgi_request)]
        self.assertIn("You are now the representative for this organization.", messages)

    def test_claiming_already_claimed_org_shows_already_claimed_message(self) -> None:
        organization = Organization.objects.create(
            name="Already Claimed Org",
            representative="carol",
            status=Organization.Status.active,
            claim_secret="already-claimed-secret",
        )
        token = make_organization_claim_token(organization)

        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.views_organizations.block_action_without_coc", return_value=None),
            patch("core.views_organizations.block_action_without_country_code", return_value=None),
            patch("core.views_organizations.FreeIPAUser.get", return_value=alice),
        ):
            response = self.client.post(reverse("organization-claim", args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "This organization has already been claimed. If you need access, contact the Membership Committee.",
        )
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "carol")

    def test_claim_page_displays_em_dash_for_empty_contact_email(self) -> None:
        organization = Organization.objects.create(name="No Contact Org")
        token = make_organization_claim_token(organization)

        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.views_organizations.block_action_without_coc", return_value=None),
            patch("core.views_organizations.block_action_without_country_code", return_value=None),
            patch("core.views_organizations.FreeIPAUser.get", return_value=alice),
        ):
            response = self.client.get(reverse("organization-claim", args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contact email")
        self.assertContains(response, "<dd class=\"col-sm-8\">\n                    —\n                </dd>", html=True)

    def test_claim_requires_country_and_redirects_to_profile_when_missing(self) -> None:
        organization = Organization.objects.create(name="Country Gate Org")
        token = make_organization_claim_token(organization)

        self._login_as_freeipa_user("alice")
        alice_without_country = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        with (
            patch("core.views_utils.has_signed_coc", return_value=True),
            patch("core.views_organizations.FreeIPAUser.get", return_value=alice_without_country),
        ):
            response = self.client.get(reverse("organization-claim", args=[token]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('settings')}?tab=profile&highlight=country_code")

    @override_settings(COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN="code-of-conduct")
    def test_claim_requires_signed_coc(self) -> None:
        organization = Organization.objects.create(name="CoC Gate Org")
        token = make_organization_claim_token(organization)

        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.views_utils.has_signed_coc", return_value=False),
            patch("core.views_organizations.FreeIPAUser.get", return_value=alice),
        ):
            response = self.client.get(reverse("organization-claim", args=[token]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('settings')}?tab=agreements&agreement=code-of-conduct")
