from unittest.mock import patch
from urllib.parse import quote_plus

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP


class OrganizationListPaginationTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_organizations_list_paginates_with_stable_ordering(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        for index in range(26):
            Organization.objects.create(
                name=f"Org {index:02d}",
                representative=f"rep-{index}",
            )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_1_response = self.client.get(reverse("organizations"))

        self.assertEqual(page_1_response.status_code, 200)
        self.assertTrue(page_1_response.context["is_paginated"])
        self.assertEqual(page_1_response.context["page_obj"].number, 1)
        self.assertEqual(page_1_response.context["paginator"].num_pages, 2)

        page_1_names = [org.name for org in page_1_response.context["organizations"]]
        expected_names = [f"Org {index:02d}" for index in range(26)]
        self.assertEqual(page_1_names, expected_names[:25])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_2_response = self.client.get(reverse("organizations"), {"page": "2"})

        self.assertEqual(page_2_response.status_code, 200)
        self.assertEqual(page_2_response.context["page_obj"].number, 2)

        page_2_names = [org.name for org in page_2_response.context["organizations"]]
        self.assertEqual(page_2_names, expected_names[25:])

    def test_organizations_list_preserves_query_string_across_pagination(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        for index in range(26):
            Organization.objects.create(
                name=f"Match Org {index:02d}",
                representative=f"rep-match-{index}",
            )
        for index in range(3):
            Organization.objects.create(
                name=f"Other Org {index:02d}",
                representative=f"rep-other-{index}",
            )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_1_response = self.client.get(reverse("organizations"), {"q": "match"})

        self.assertEqual(page_1_response.status_code, 200)
        self.assertTrue(page_1_response.context["is_paginated"])
        self.assertEqual(page_1_response.context["page_obj"].number, 1)
        self.assertIn("q=match", page_1_response.context["page_url_prefix"])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_2_response = self.client.get(reverse("organizations"), {"q": "match", "page": "2"})

        self.assertEqual(page_2_response.status_code, 200)
        self.assertEqual(page_2_response.context["page_obj"].number, 2)
        page_2_names = [org.name for org in page_2_response.context["organizations"]]
        self.assertEqual(page_2_names, ["Match Org 25"])

    def test_organizations_list_non_manager_shows_only_represented_orgs(self) -> None:
        self._login_as_freeipa_user("alice")

        Organization.objects.create(
            name="Alice Org 00",
            representative="alice",
        )
        for index in range(5):
            Organization.objects.create(
                name=f"Bob Org {index:02d}",
                representative=f"bob-{index}",
            )

        representative = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=representative):
            page_1_response = self.client.get(reverse("organizations"))

        self.assertEqual(page_1_response.status_code, 200)
        self.assertFalse(page_1_response.context["is_paginated"])
        self.assertEqual(page_1_response.context["page_obj"].number, 1)
        self.assertEqual(page_1_response.context["paginator"].num_pages, 1)
        self.assertEqual(len(page_1_response.context["organizations"]), 1)
        self.assertTrue(all(org.representative == "alice" for org in page_1_response.context["organizations"]))

    def test_manager_can_filter_organizations_by_claimed_status_token(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        Organization.objects.create(
            name="Bravo Claimed",
            representative="bravo-rep",
        )

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        query = "is:claimed acme"
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("organizations"), {"q": query})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [organization.name for organization in response.context["organizations"]],
            ["Acme Claimed"],
        )
        self.assertIn(f"q={quote_plus(query)}", response.context["page_url_prefix"])

    def test_manager_can_filter_organizations_by_unclaimed_status_token(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        Organization.objects.create(
            name="Bravo Unclaimed",
            representative="",
        )

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("organizations"), {"q": "is:unclaimed acme"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [organization.name for organization in response.context["organizations"]],
            ["Acme Unclaimed"],
        )

    def test_view_only_user_can_filter_by_claimed_status_token_only_query(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        Organization.objects.create(
            name="Bravo Claimed",
            representative="bravo-rep",
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organizations"), {"q": "is:claimed"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [organization.name for organization in response.context["organizations"]],
            ["Acme Claimed", "Bravo Claimed"],
        )

    def test_view_only_user_gets_no_results_for_conflicting_status_tokens(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("organizations"),
                {"q": "is:claimed is:unclaimed"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["organizations"]), [])

    def test_manager_treats_unknown_status_token_as_plain_text(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("organizations"), {"q": "is:pending acme"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["organizations"]), [])
