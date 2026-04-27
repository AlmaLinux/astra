from unittest.mock import patch

from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import Membership, MembershipType, MembershipTypeCategory, Organization


class MembershipRequestFormApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"name": "Individual", "is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={"name": "Mirror", "is_individual": True, "is_organization": True, "sort_order": 1},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={"name": "Sponsorship", "is_individual": False, "is_organization": True, "sort_order": 2},
        )

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "group_cn": "almalinux-silver",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_request_page_bootstrap_uses_canonical_form_detail_endpoint(self) -> None:
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": [], "c": ["US"]},
        )
        self._login_as_freeipa_user("alice")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_membership.user.block_action_without_coc", return_value=None),
            patch("core.views_membership.user.block_action_without_country_code", return_value=None),
        ):
            page_response = self.client.get(reverse("membership-request"))
            api_response = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'data-membership-request-form-root=""')
        self.assertContains(
            page_response,
            f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"',
        )
        self.assertContains(
            page_response,
            f'data-membership-request-form-cancel-url="{reverse("user-profile", kwargs={"username": "alice"})}"',
        )
        self.assertContains(page_response, f'data-membership-request-form-submit-url="{reverse("membership-request")}"')
        self.assertContains(page_response, f'data-membership-request-form-privacy-policy-url="{reverse("privacy-policy")}"')
        self.assertNotContains(page_response, 'id="id_membership_type"')

        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertIsNone(payload["organization"])
        self.assertFalse(payload["no_types_available"])
        self.assertIsNone(payload["prefill_type_unavailable_name"])
        self.assertEqual(payload["form"]["fields"][0]["name"], "membership_type")
        self.assertEqual(payload["form"]["fields"][0]["widget"], "select")
        option_values = [
            option["value"]
            for group in payload["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertEqual(option_values, ["individual", "mirror"])
        self.assertNotIn("cancel_url", payload)
        self.assertNotIn("submit_url", payload)
        self.assertNotIn("privacy_policy_url", payload)

    def test_organization_membership_request_bootstrap_uses_org_detail_endpoint_and_prefills_membership_type(self) -> None:
        organization = Organization.objects.create(name="Acme Org", representative="bob")
        Membership.objects.create(target_organization=organization, membership_type_id="silver")

        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": [], "c": ["US"]},
        )
        self._login_as_freeipa_user("bob")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.user.block_action_without_coc", return_value=None),
            patch("core.views_membership.user.block_action_without_country_code", return_value=None),
        ):
            page_response = self.client.get(reverse("organization-membership-request", args=[organization.pk]))
            api_response = self.client.get(reverse("api-organization-membership-request-form-detail", args=[organization.pk]))

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'data-membership-request-form-root=""')
        self.assertContains(
            page_response,
            f'data-membership-request-form-api-url="{reverse("api-organization-membership-request-form-detail", args=[organization.pk])}"',
        )
        self.assertContains(
            page_response,
            f'data-membership-request-form-cancel-url="{reverse("organization-detail", kwargs={"organization_id": organization.pk})}"',
        )
        self.assertContains(
            page_response,
            f'data-membership-request-form-submit-url="{reverse("organization-membership-request", args=[organization.pk])}"',
        )
        self.assertNotContains(page_response, 'id="id_membership_type"')

        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertEqual(payload["organization"], {"id": organization.pk, "name": "Acme Org"})
        self.assertEqual(payload["form"]["fields"][0]["value"], "silver")
        option_values = [
            option["value"]
            for group in payload["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertIn("mirror", option_values)
        self.assertNotIn("cancel_url", payload)
        self.assertNotIn("submit_url", payload)
        self.assertNotIn("rescind_url", payload)

    def test_membership_request_form_detail_api_rejects_non_get_with_json_and_private_no_cache(self) -> None:
        self._login_as_freeipa_user("alice")

        response = self.client.post(reverse("api-membership-request-form-detail"))

        self.assertEqual(response.status_code, 405)
        self.assertJSONEqual(response.content, {"error": "Method not allowed."})
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_membership_request_form_detail_api_returns_json_not_found_when_create_access_is_denied(self) -> None:
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": [], "c": ["US"]},
        )
        self._login_as_freeipa_user("alice")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_membership.user.block_action_without_coc", return_value=HttpResponse("blocked", status=403)),
        ):
            response = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(response.status_code, 404)
        self.assertJSONEqual(response.content, {"error": "Not found."})
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_organization_membership_request_form_detail_api_rejects_non_get_with_json_and_private_no_cache(self) -> None:
        organization = Organization.objects.create(name="Acme Org", representative="bob")
        self._login_as_freeipa_user("bob")

        with patch(
            "core.freeipa.user.FreeIPAUser.get",
            return_value=FreeIPAUser("bob", {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": [], "c": ["US"]}),
        ):
            response = self.client.post(reverse("api-organization-membership-request-form-detail", args=[organization.pk]))

        self.assertEqual(response.status_code, 405)
        self.assertJSONEqual(response.content, {"error": "Method not allowed."})
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_organization_membership_request_form_detail_api_returns_json_not_found_for_non_representative(self) -> None:
        organization = Organization.objects.create(name="Acme Org", representative="bob")
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": [], "c": ["US"]},
        )
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            response = self.client.get(reverse("api-organization-membership-request-form-detail", args=[organization.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertJSONEqual(response.content, {"error": "Not found."})
        self.assertEqual(response["Cache-Control"], "private, no-cache")