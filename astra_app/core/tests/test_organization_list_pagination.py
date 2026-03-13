import datetime
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import quote_plus

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType, MembershipTypeCategory, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP


class _FormInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._forms: list[list[dict[str, str]]] = []
        self._current_form_inputs: list[dict[str, str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self._current_form_inputs = []
            return

        if tag != "input" or self._current_form_inputs is None:
            return

        normalized_attrs = {
            attr_name: attr_value or ""
            for attr_name, attr_value in attrs
            if attr_name
        }
        self._current_form_inputs.append(normalized_attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag != "form" or self._current_form_inputs is None:
            return

        self._forms.append(self._current_form_inputs)
        self._current_form_inputs = None

    def hidden_fields_for_search_field(self, search_field_name: str) -> dict[str, str]:
        for form_inputs in self._forms:
            has_target_search_field = any(
                attrs.get("name") == search_field_name and attrs.get("type", "text") == "text"
                for attrs in form_inputs
            )
            if not has_target_search_field:
                continue

            return {
                attrs["name"]: attrs.get("value", "")
                for attrs in form_inputs
                if attrs.get("type") == "hidden" and attrs.get("name")
            }

        return {}


class OrganizationListPaginationTests(TestCase):
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

    def _get_organizations_render_context(self, response) -> dict[str, object]:
        for context_layer in response.context:
            if "sponsor_organizations" in context_layer and "mirror_organizations" in context_layer:
                return context_layer
        self.fail("Organizations render context was not found in response context layers")

    def _assert_no_legacy_unified_context(self, response) -> None:
        render_context = self._get_organizations_render_context(response)
        for key in (
            "organizations",
            "page_obj",
            "paginator",
            "is_paginated",
            "page_numbers",
            "show_first",
            "show_last",
            "page_url_prefix",
            "q",
        ):
            self.assertFalse(key in render_context, f"Legacy context key {key} is still present")

    def _hidden_fields_for_search_form(self, response, search_field_name: str) -> dict[str, str]:
        parser = _FormInputParser()
        parser.feed(response.content.decode("utf-8"))
        return parser.hidden_fields_for_search_field(search_field_name)

    def test_organizations_list_paginates_with_stable_ordering(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        for index in range(26):
            organization = Organization.objects.create(
                name=f"Org {index:02d}",
                representative=f"rep-{index}",
            )
            Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_1_response = self.client.get(reverse("organizations"))

        self.assertEqual(page_1_response.status_code, 200)
        self._assert_no_legacy_unified_context(page_1_response)
        self.assertTrue(page_1_response.context["sponsor_is_paginated"])
        self.assertEqual(page_1_response.context["sponsor_page_obj"].number, 1)
        self.assertEqual(page_1_response.context["sponsor_paginator"].num_pages, 2)

        page_1_names = [org.name for org in page_1_response.context["sponsor_organizations"]]
        expected_names = [f"Org {index:02d}" for index in range(26)]
        self.assertEqual(page_1_names, expected_names[:25])
        self.assertEqual(list(page_1_response.context["mirror_organizations"]), [])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_2_response = self.client.get(reverse("organizations"), {"page_sponsor": "2"})

        self.assertEqual(page_2_response.status_code, 200)
        self.assertEqual(page_2_response.context["sponsor_page_obj"].number, 2)

        page_2_names = [org.name for org in page_2_response.context["sponsor_organizations"]]
        self.assertEqual(page_2_names, expected_names[25:])

    def test_organizations_list_preserves_query_string_across_pagination(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        for index in range(26):
            match_org = Organization.objects.create(
                name=f"Match Org {index:02d}",
                representative=f"rep-match-{index}",
            )
            Membership.objects.create(target_organization=match_org, membership_type=sponsor_type)
        for index in range(3):
            other_org = Organization.objects.create(
                name=f"Other Org {index:02d}",
                representative=f"rep-other-{index}",
            )
            Membership.objects.create(target_organization=other_org, membership_type=sponsor_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_1_response = self.client.get(
                reverse("organizations"),
                {"q_sponsor": "match", "q_mirror": "mirror-term", "page_mirror": "2"},
            )

        self.assertEqual(page_1_response.status_code, 200)
        self._assert_no_legacy_unified_context(page_1_response)
        self.assertTrue(page_1_response.context["sponsor_is_paginated"])
        self.assertEqual(page_1_response.context["sponsor_page_obj"].number, 1)
        self.assertIn("q_sponsor=match", page_1_response.context["sponsor_page_url_prefix"])
        self.assertIn("q_mirror=mirror-term", page_1_response.context["sponsor_page_url_prefix"])
        self.assertIn("page_mirror=2", page_1_response.context["sponsor_page_url_prefix"])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            page_2_response = self.client.get(
                reverse("organizations"),
                {"q_sponsor": "match", "page_sponsor": "2"},
            )

        self.assertEqual(page_2_response.status_code, 200)
        self.assertEqual(page_2_response.context["sponsor_page_obj"].number, 2)
        page_2_names = [org.name for org in page_2_response.context["sponsor_organizations"]]
        self.assertEqual(page_2_names, ["Match Org 25"])

    def test_organizations_list_non_manager_shows_only_claimed_orgs(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        claimed_mirror = Organization.objects.create(name="Claimed Mirror", representative="alice")
        claimed_sponsor = Organization.objects.create(name="Claimed Sponsor", representative="bob")
        Organization.objects.create(name="Claimed No Membership", representative="carol")
        unclaimed_mirror = Organization.objects.create(name="Unclaimed Mirror", representative="")

        Membership.objects.create(target_organization=claimed_mirror, membership_type=mirror_type)
        Membership.objects.create(target_organization=claimed_sponsor, membership_type=sponsor_type)
        Membership.objects.create(target_organization=unclaimed_mirror, membership_type=mirror_type)

        representative = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=representative):
            page_1_response = self.client.get(reverse("organizations"))

        self.assertEqual(page_1_response.status_code, 200)
        self._assert_no_legacy_unified_context(page_1_response)
        self.assertFalse(page_1_response.context["sponsor_is_paginated"])
        self.assertFalse(page_1_response.context["mirror_is_paginated"])
        self.assertEqual(page_1_response.context["sponsor_page_obj"].number, 1)
        self.assertEqual(page_1_response.context["mirror_page_obj"].number, 1)
        lower_orgs = [
            *page_1_response.context["sponsor_organizations"],
            *page_1_response.context["mirror_organizations"],
        ]
        self.assertEqual(len(lower_orgs), 2)
        self.assertTrue(all(organization.status == Organization.Status.active for organization in lower_orgs))

    def test_organizations_split_card_context_is_authoritative(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        organization = Organization.objects.create(name="Sponsor Org", representative="rep")
        Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self._assert_no_legacy_unified_context(response)

    def test_manager_can_filter_organizations_by_claimed_status_token(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        acme_claimed = Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        acme_unclaimed = Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        bravo_claimed = Organization.objects.create(
            name="Bravo Claimed",
            representative="bravo-rep",
        )
        for organization in (acme_claimed, acme_unclaimed, bravo_claimed):
            Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        query = "is:claimed acme"
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("organizations"), {"q_sponsor": query})

        self.assertEqual(response.status_code, 200)
        self._assert_no_legacy_unified_context(response)
        self.assertEqual(
            [organization.name for organization in response.context["sponsor_organizations"]],
            ["Acme Claimed"],
        )
        self.assertIn(f"q_sponsor={quote_plus(query)}", response.context["sponsor_page_url_prefix"])

    def test_manager_can_filter_organizations_by_unclaimed_status_token(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        acme_claimed = Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        acme_unclaimed = Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        bravo_unclaimed = Organization.objects.create(
            name="Bravo Unclaimed",
            representative="",
        )
        for organization in (acme_claimed, acme_unclaimed, bravo_unclaimed):
            Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("organizations"), {"q_sponsor": "is:unclaimed acme"})

        self.assertEqual(response.status_code, 200)
        self._assert_no_legacy_unified_context(response)
        self.assertEqual(
            [organization.name for organization in response.context["sponsor_organizations"]],
            ["Acme Unclaimed"],
        )

    def test_view_only_user_can_filter_by_claimed_status_token_only_query(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        acme_claimed = Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        acme_unclaimed = Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        bravo_claimed = Organization.objects.create(
            name="Bravo Claimed",
            representative="bravo-rep",
        )
        for organization in (acme_claimed, acme_unclaimed, bravo_claimed):
            Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organizations"), {"q_sponsor": "is:claimed"})

        self.assertEqual(response.status_code, 200)
        self._assert_no_legacy_unified_context(response)
        self.assertEqual(
            [organization.name for organization in response.context["sponsor_organizations"]],
            ["Acme Claimed", "Bravo Claimed"],
        )

    def test_view_only_user_gets_no_results_for_conflicting_status_tokens(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        acme_claimed = Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        acme_unclaimed = Organization.objects.create(
            name="Acme Unclaimed",
            representative="",
        )
        for organization in (acme_claimed, acme_unclaimed):
            Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("organizations"),
                {"q_sponsor": "is:claimed is:unclaimed"},
            )

        self.assertEqual(response.status_code, 200)
        self._assert_no_legacy_unified_context(response)
        self.assertEqual(list(response.context["sponsor_organizations"]), [])

    def test_manager_treats_unknown_status_token_as_plain_text(self) -> None:
        _, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

        organization = Organization.objects.create(
            name="Acme Claimed",
            representative="acme-rep",
        )
        Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.get(reverse("organizations"), {"q_sponsor": "is:pending acme"})

        self.assertEqual(response.status_code, 200)
        self._assert_no_legacy_unified_context(response)
        self.assertEqual(list(response.context["sponsor_organizations"]), [])

    def test_manager_can_filter_organizations_by_membership_type_tokens(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="manager",
        )
        self._login_as_freeipa_user("manager")

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
        MembershipTypeCategory.objects.update_or_create(
            pk="community",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 2,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="sponsor",
            defaults={
                "name": "Sponsor",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "community",
                "sort_order": 0,
                "enabled": True,
            },
        )

        mirror_org = Organization.objects.create(name="Acme Mirror", representative="rep-mirror")
        sponsor_org = Organization.objects.create(name="Acme Sponsor", representative="rep-sponsor")
        gold_org = Organization.objects.create(name="Acme Gold", representative="rep-gold")
        expired_org = Organization.objects.create(name="Acme Mirror Expired", representative="rep-expired")

        Membership.objects.create(target_organization=mirror_org, membership_type_id="mirror")
        Membership.objects.create(target_organization=sponsor_org, membership_type_id="sponsor")
        Membership.objects.create(target_organization=gold_org, membership_type_id="gold")
        Membership.objects.create(
            target_organization=expired_org,
            membership_type_id="mirror",
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        manager = FreeIPAUser(
            "manager",
            {"uid": ["manager"], "memberof_group": [], "c": ["US"]},
        )

        expected_results = {
            "is:mirror acme": ([], ["Acme Mirror"]),
            "is:sponsor acme": (["Acme Sponsor"], []),
            "is:gold acme": ([], ["Acme Gold"]),
        }

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            for query, (expected_sponsor_names, expected_mirror_names) in expected_results.items():
                with self.subTest(query=query):
                    response = self.client.get(
                        reverse("organizations"),
                        {
                            "q_sponsor": query,
                            "q_mirror": query,
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self._assert_no_legacy_unified_context(response)
                    self.assertEqual(
                        [organization.name for organization in response.context["sponsor_organizations"]],
                        expected_sponsor_names,
                    )
                    self.assertEqual(
                        [organization.name for organization in response.context["mirror_organizations"]],
                        expected_mirror_names,
                    )

    def test_regular_user_can_access_organizations_page(self) -> None:
        self._login_as_freeipa_user("alice")

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Organization")

    def test_organizations_page_accessible_when_authenticated_username_resolution_is_empty(self) -> None:
        self._login_as_freeipa_user("alice")

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            with patch("core.views_organizations.get_username", return_value=""):
                response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Organization")

    def test_regular_user_top_card_shows_create_button_when_not_representative(self) -> None:
        self._login_as_freeipa_user("alice")

        Organization.objects.create(
            name="Claimed Org",
            representative="bob",
        )

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Organization")
        self.assertContains(response, reverse("organization-create"))
        self.assertContains(
            response,
            "Create an organization profile only if you are an employee or authorized representative of the organization applying to sponsor AlmaLinux.",
        )

    def test_representative_top_card_shows_represented_organization_widget(self) -> None:
        self._login_as_freeipa_user("alice")

        Organization.objects.create(
            name="Alice Org",
            representative="alice",
        )

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Organization")
        self.assertContains(response, "Alice Org")
        self.assertNotContains(response, reverse("organization-create"))

    def test_regular_user_lower_section_shows_claimed_only_without_detail_links(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        mirror_only = Organization.objects.create(name="Mirror Only", representative="bob")
        sponsor_only = Organization.objects.create(name="Sponsor Only", representative="carol")
        sponsor_and_mirror = Organization.objects.create(name="Sponsor And Mirror", representative="dave")
        Organization.objects.create(name="Claimed No Membership", representative="erin")
        unclaimed_with_mirror = Organization.objects.create(name="Unclaimed With Mirror", representative="")

        Membership.objects.create(target_organization=mirror_only, membership_type=mirror_type)
        Membership.objects.create(target_organization=sponsor_only, membership_type=sponsor_type)
        Membership.objects.create(target_organization=sponsor_and_mirror, membership_type=sponsor_type)
        Membership.objects.create(target_organization=sponsor_and_mirror, membership_type=mirror_type)
        Membership.objects.create(target_organization=unclaimed_with_mirror, membership_type=mirror_type)

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AlmaLinux Sponsor Members")
        self.assertContains(response, "Mirror Sponsor Members")

        sponsor_names = {organization.name for organization in response.context["sponsor_organizations"]}
        mirror_names = {organization.name for organization in response.context["mirror_organizations"]}
        self.assertEqual(sponsor_names, {"Sponsor Only", "Sponsor And Mirror"})
        self.assertEqual(mirror_names, {"Mirror Only"})

        self.assertNotContains(response, reverse("organization-detail", args=[mirror_only.pk]))
        self.assertNotContains(response, reverse("organization-detail", args=[sponsor_only.pk]))
        self.assertNotContains(response, reverse("organization-detail", args=[sponsor_and_mirror.pk]))

    def test_regular_user_claimed_organizations_empty_state_copy(self) -> None:
        self._ensure_org_membership_types()
        self._login_as_freeipa_user("alice")

        Organization.objects.create(name="Claimed Without Membership", representative="bob")
        Organization.objects.create(name="Unclaimed Without Membership", representative="")

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No AlmaLinux sponsor members found.")
        self.assertContains(response, "No mirror sponsor members found.")

    def test_committee_lower_section_shows_all_orgs_with_links_and_default_empty_copy(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        mirror_only = Organization.objects.create(name="Mirror Only", representative="bob")
        sponsor_only = Organization.objects.create(name="Sponsor Only", representative="carol")
        sponsor_and_mirror = Organization.objects.create(name="Sponsor And Mirror", representative="dave")
        no_membership = Organization.objects.create(name="No Membership", representative="erin")
        Organization.objects.create(name="Unclaimed Without Membership", representative="")

        Membership.objects.create(target_organization=mirror_only, membership_type=mirror_type)
        Membership.objects.create(target_organization=sponsor_only, membership_type=sponsor_type)
        Membership.objects.create(target_organization=sponsor_and_mirror, membership_type=sponsor_type)
        Membership.objects.create(target_organization=sponsor_and_mirror, membership_type=mirror_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AlmaLinux Sponsor Members")
        self.assertContains(response, "Mirror Sponsor Members")

        sponsor_names = {organization.name for organization in response.context["sponsor_organizations"]}
        mirror_names = {organization.name for organization in response.context["mirror_organizations"]}
        self.assertEqual(sponsor_names, {"Sponsor Only", "Sponsor And Mirror"})
        self.assertEqual(
            mirror_names,
            {
                "Mirror Only",
                "No Membership",
                "Unclaimed Without Membership",
            },
        )

        self.assertContains(response, reverse("organization-detail", args=[mirror_only.pk]))
        self.assertContains(response, reverse("organization-detail", args=[sponsor_only.pk]))
        self.assertContains(response, reverse("organization-detail", args=[sponsor_and_mirror.pk]))
        self.assertContains(response, reverse("organization-detail", args=[no_membership.pk]))

        Organization.objects.all().delete()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            empty_response = self.client.get(reverse("organizations"))

        self.assertEqual(empty_response.status_code, 200)
        self.assertContains(empty_response, "No AlmaLinux sponsor members found.")
        self.assertContains(
            empty_response,
            "No mirror sponsor members or organizations without memberships found.",
        )

    def test_lower_cards_use_independent_search_and_pagination_query_params(self) -> None:
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

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("organizations"),
                {
                    "q_sponsor": "Sponsor Org",
                    "page_sponsor": "2",
                    "q_mirror": "Mirror Org",
                    "page_mirror": "2",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="q_sponsor"')
        self.assertContains(response, 'name="q_mirror"')

        sponsor_hidden_fields = self._hidden_fields_for_search_form(response, "q_sponsor")
        mirror_hidden_fields = self._hidden_fields_for_search_form(response, "q_mirror")
        self.assertEqual(sponsor_hidden_fields.get("q_mirror"), "Mirror Org")
        self.assertEqual(sponsor_hidden_fields.get("page_mirror"), "2")
        self.assertEqual(mirror_hidden_fields.get("q_sponsor"), "Sponsor Org")
        self.assertEqual(mirror_hidden_fields.get("page_sponsor"), "2")

        self.assertEqual(response.context["sponsor_page_obj"].number, 2)
        self.assertEqual(response.context["mirror_page_obj"].number, 2)
        self.assertEqual(response.context["sponsor_paginator"].num_pages, 2)
        self.assertEqual(response.context["mirror_paginator"].num_pages, 2)

        self.assertIn("q_mirror=Mirror+Org", response.context["sponsor_page_url_prefix"])
        self.assertIn("page_mirror=2", response.context["sponsor_page_url_prefix"])
        self.assertIn("q_sponsor=Sponsor+Org", response.context["mirror_page_url_prefix"])
        self.assertIn("page_sponsor=2", response.context["mirror_page_url_prefix"])

    def test_legacy_q_and_page_params_fallback_to_sponsor_card(self) -> None:
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

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("organizations"),
                {
                    "q": "Sponsor Org",
                    "page": "2",
                    "q_mirror": "Mirror Org",
                    "page_mirror": "2",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["q_sponsor"], "Sponsor Org")
        self.assertEqual(response.context["q_mirror"], "Mirror Org")
        self.assertEqual(response.context["sponsor_page_obj"].number, 2)
        self.assertEqual(response.context["mirror_page_obj"].number, 2)
        self.assertEqual([org.name for org in response.context["sponsor_organizations"]], ["Sponsor Org 25"])
        self.assertEqual([org.name for org in response.context["mirror_organizations"]], ["Mirror Org 25"])

        self.assertIn("q_sponsor=Sponsor+Org", response.context["sponsor_page_url_prefix"])
        self.assertIn("q_mirror=Mirror+Org", response.context["sponsor_page_url_prefix"])
        self.assertIn("page_mirror=2", response.context["sponsor_page_url_prefix"])
        self.assertNotIn("q=Sponsor+Org", response.context["sponsor_page_url_prefix"])
        self.assertNotIn("&page=2", response.context["sponsor_page_url_prefix"])

    def test_mirror_search_filters_only_mirror_results(self) -> None:
        mirror_type, sponsor_type = self._ensure_org_membership_types()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        sponsor_alpha = Organization.objects.create(name="Sponsor Alpha", representative="s-alpha")
        sponsor_beta = Organization.objects.create(name="Sponsor Beta", representative="s-beta")
        mirror_match = Organization.objects.create(name="Mirror Match", representative="m-match")
        mirror_other = Organization.objects.create(name="Mirror Other", representative="m-other")

        Membership.objects.create(target_organization=sponsor_alpha, membership_type=sponsor_type)
        Membership.objects.create(target_organization=sponsor_beta, membership_type=sponsor_type)
        Membership.objects.create(target_organization=mirror_match, membership_type=mirror_type)
        Membership.objects.create(target_organization=mirror_other, membership_type=mirror_type)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organizations"), {"q_mirror": "Match"})

        self.assertEqual(response.status_code, 200)
        sponsor_names = {organization.name for organization in response.context["sponsor_organizations"]}
        mirror_names = {organization.name for organization in response.context["mirror_organizations"]}
        self.assertEqual(sponsor_names, {"Sponsor Alpha", "Sponsor Beta"})
        self.assertEqual(mirror_names, {"Mirror Match"})

    def test_org_widget_badges_show_sponsorship_before_mirror(self) -> None:
        self._ensure_org_membership_types()
        sponsor_priority = MembershipType.objects.create(
            code="sponsor-priority",
            name="Sponsor Priority Badge",
            category_id="sponsorship",
            sort_order=20,
            enabled=True,
        )
        mirror_priority = MembershipType.objects.create(
            code="mirror-priority",
            name="Mirror Priority Badge",
            category_id="mirror",
            sort_order=10,
            enabled=True,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        self._login_as_freeipa_user("reviewer")

        org = Organization.objects.create(name="Badge Order Org", representative="org-rep")
        Membership.objects.create(target_organization=org, membership_type=mirror_priority)
        Membership.objects.create(target_organization=org, membership_type=sponsor_priority)

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organizations"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        org_position = body.find("Badge Order Org")
        self.assertGreaterEqual(org_position, 0)

        sponsor_position = body.find("Sponsor Priority Badge", org_position)
        mirror_position = body.find("Mirror Priority Badge", org_position)
        self.assertGreaterEqual(sponsor_position, 0)
        self.assertGreaterEqual(mirror_position, 0)
        self.assertLess(
            sponsor_position,
            mirror_position,
            "Expected sponsorship badge to render before mirror badge.",
        )
