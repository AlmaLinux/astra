import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType, MembershipTypeCategory, Organization
from core.permissions import ASTRA_VIEW_MEMBERSHIP


class OrganizationsVueTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _ensure_org_membership_types(self) -> tuple[MembershipType, MembershipType]:
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
        mirror_type, _ = MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
            },
        )
        sponsor_type, _ = MembershipType.objects.update_or_create(
            code="sponsor",
            defaults={
                "name": "Sponsor",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        return mirror_type, sponsor_type

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_organizations_page_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("alice")
        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-organizations-root")
        self.assertContains(response, 'data-organizations-api-url="/api/v1/organizations"')
        self.assertContains(response, 'data-organizations-detail-url-template="/organization/__organization_id__/"')
        self.assertContains(response, 'data-organizations-create-url="/organizations/create/"')
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/organizations.ts"')
        self.assertContains(response, "Loading organizations...")
        self.assertNotContains(response, "AlmaLinux Sponsor Members")
        self.assertNotContains(response, "Mirror Sponsor Members")
        self.assertNotContains(response, "Create organization")

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_organizations_page_shell_does_not_prerender_organization_cards(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        sponsor_org = Organization.objects.create(name="Sponsor Org", representative="bob")
        mirror_org = Organization.objects.create(name="Mirror Org", representative="carol")
        Organization.objects.create(name="Alice Org", representative="alice")

        Membership.objects.create(target_organization=sponsor_org, membership_type=sponsor_type)
        Membership.objects.create(target_organization=mirror_org, membership_type=mirror_type)

        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Alice Org")
        self.assertNotContains(response, "Sponsor Org")
        self.assertNotContains(response, "Mirror Org")
        self.assertContains(response, 'data-organizations-create-url="/organizations/create/"')

    def test_organizations_api_returns_split_card_payload_for_regular_user(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        sponsor_org = Organization.objects.create(name="Sponsor Org", representative="bob")
        mirror_org = Organization.objects.create(name="Mirror Org", representative="carol")
        Organization.objects.create(name="Alice Org", representative="alice")
        unclaimed_org = Organization.objects.create(name="Unclaimed Org", representative="")

        Membership.objects.create(target_organization=sponsor_org, membership_type=sponsor_type)
        Membership.objects.create(target_organization=mirror_org, membership_type=mirror_type)
        Membership.objects.create(target_organization=unclaimed_org, membership_type=mirror_type)

        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(reverse("api-organizations"), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["my_organization"]["name"], "Alice Org")
        self.assertNotIn("detail_url", payload["my_organization"])
        self.assertNotIn("my_organization_create_url", payload)
        self.assertEqual([item["name"] for item in payload["sponsor_card"]["items"]], ["Sponsor Org"])
        self.assertEqual([item["name"] for item in payload["mirror_card"]["items"]], ["Mirror Org"])
        self.assertNotIn("detail_url", payload["sponsor_card"]["items"][0])
        self.assertNotIn("detail_url", payload["mirror_card"]["items"][0])
        self.assertFalse(payload["sponsor_card"]["items"][0]["link_to_detail"])
        self.assertFalse(payload["mirror_card"]["items"][0]["link_to_detail"])
        self.assertEqual(payload["sponsor_card"]["empty_label"], "No AlmaLinux sponsor members found.")
        self.assertEqual(payload["mirror_card"]["empty_label"], "No mirror sponsor members found.")

    def test_organizations_api_returns_create_url_when_viewer_has_no_org(self) -> None:
        _mirror_type, sponsor_type = self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        sponsor_org = Organization.objects.create(name="Sponsor Org", representative="bob")
        Membership.objects.create(target_organization=sponsor_org, membership_type=sponsor_type)

        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(reverse("api-organizations"), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertIsNone(payload["my_organization"])
        self.assertNotIn("my_organization_create_url", payload)

    def test_organizations_api_manager_can_filter_claimed_status_token(self) -> None:
        _mirror_type, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        claimed_org = Organization.objects.create(name="Acme Claimed", representative="rep")
        unclaimed_org = Organization.objects.create(name="Acme Unclaimed", representative="")
        Membership.objects.create(target_organization=claimed_org, membership_type=sponsor_type)
        Membership.objects.create(target_organization=unclaimed_org, membership_type=sponsor_type)

        manager = FreeIPAUser("manager", {"uid": ["manager"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("api-organizations"), {"q_sponsor": "is:claimed acme"}, HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual([item["name"] for item in payload["sponsor_card"]["items"]], ["Acme Claimed"])

    def test_organizations_api_preserves_independent_search_and_pagination_state(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        for index in range(26):
            sponsor_org = Organization.objects.create(name=f"Sponsor Org {index:02d}", representative=f"s-{index}")
            mirror_org = Organization.objects.create(name=f"Mirror Org {index:02d}", representative=f"m-{index}")
            Membership.objects.create(target_organization=sponsor_org, membership_type=sponsor_type)
            Membership.objects.create(target_organization=mirror_org, membership_type=mirror_type)

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-organizations"),
                {
                    "q_sponsor": "Sponsor Org",
                    "page_sponsor": "2",
                    "q_mirror": "Mirror Org",
                    "page_mirror": "2",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["sponsor_card"]["q"], "Sponsor Org")
        self.assertEqual(payload["mirror_card"]["q"], "Mirror Org")
        self.assertEqual(payload["sponsor_card"]["pagination"]["page"], 2)
        self.assertEqual(payload["mirror_card"]["pagination"]["page"], 2)

    def test_organizations_api_sponsor_badges_sort_before_mirror_badges(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        org = Organization.objects.create(name="Badge Order Org", representative="org-rep")
        Membership.objects.create(target_organization=org, membership_type=mirror_type)
        Membership.objects.create(target_organization=org, membership_type=sponsor_type)

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("api-organizations"), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        badges = payload["sponsor_card"]["items"][0]["memberships"]
        self.assertEqual([badge["label"] for badge in badges], ["Sponsor", "Mirror"])

    def test_organizations_api_uses_shared_pagination_serializer_for_cards(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        sponsor_org = Organization.objects.create(name="Sponsor Org", representative="bob")
        mirror_org = Organization.objects.create(name="Mirror Org", representative="carol")
        Membership.objects.create(target_organization=sponsor_org, membership_type=sponsor_type)
        Membership.objects.create(target_organization=mirror_org, membership_type=mirror_type)

        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch(
                "core.views_organizations.serialize_pagination",
                wraps=lambda page_ctx: {"count": 123},
                create=True,
            ) as serialize_mock,
        ):
            response = self.client.get(reverse("api-organizations"), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["sponsor_card"]["pagination"], {"count": 123})
        self.assertEqual(payload["mirror_card"]["pagination"], {"count": 123})
        self.assertEqual(serialize_mock.call_count, 2)
