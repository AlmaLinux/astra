import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType, MembershipTypeCategory, Organization
from core.permissions import ASTRA_VIEW_MEMBERSHIP


class MembershipStatsDashboardTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_stats_page_requires_membership_management_permission(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer User"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("membership-stats"))

        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("users"), resp["Location"])

    def test_sidebar_shows_statistics_link_only_with_membership_management(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Management")
        self.assertContains(resp, f'href="{reverse("membership-stats")}"')
        self.assertContains(resp, f'href="{reverse("membership-sponsors")}"')

    def test_membership_sponsors_page_requires_membership_management_permission(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer User"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("membership-sponsors"))

        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("users"), resp["Location"])

    def test_membership_sponsors_page_renders_sponsorship_rows_and_freeipa_fallback(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 1,
            },
        )
        MembershipType.objects.update_or_create(
            code="sponsor-standard",
            defaults={
                "name": "Sponsor Standard",
                "group_cn": "sponsor-standard",
                "category_id": "sponsorship",
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="individual-standard",
            defaults={
                "name": "Individual Standard",
                "group_cn": "individual-standard",
                "category_id": "individual",
                "enabled": True,
            },
        )

        sponsor_org = Organization.objects.create(name="Sponsor Org", representative="repuser")
        fallback_org = Organization.objects.create(name="Fallback Org", representative="repfallback")

        Membership.objects.create(
            target_organization=sponsor_org,
            membership_type_id="sponsor-standard",
            expires_at=timezone.now() + datetime.timedelta(days=5, minutes=1),
        )
        Membership.objects.create(
            target_organization=fallback_org,
            membership_type_id="sponsor-standard",
            expires_at=None,
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual-standard",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )
        sponsor_rep = FreeIPAUser(
            "repuser",
            {
                "uid": ["repuser"],
                "displayname": ["Representative User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "repuser":
                return sponsor_rep
            if username == "repfallback":
                raise RuntimeError("FreeIPA unavailable")
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-sponsors"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sponsor Org")
        self.assertContains(resp, "Fallback Org")
        self.assertContains(resp, "Representative User (repuser)")
        self.assertContains(resp, "repfallback")
        self.assertContains(resp, f'href="{reverse("organization-detail", kwargs={"organization_id": sponsor_org.pk})}"')
        self.assertContains(resp, f'href="{reverse("user-profile", kwargs={"username": "repuser"})}"')
        self.assertContains(resp, 'id="sponsors-table"')
        self.assertContains(resp, "(5 days left)")
        self.assertContains(resp, "data-order=\"9999-12-31\"")
        self.assertNotContains(resp, "alice")

    def test_membership_sponsors_page_uses_effective_category_when_denorm_drifts(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 1,
            },
        )
        MembershipType.objects.update_or_create(
            code="sponsor-standard",
            defaults={
                "name": "Sponsor Standard",
                "group_cn": "sponsor-standard",
                "category_id": "sponsorship",
                "enabled": True,
            },
        )

        sponsor_org = Organization.objects.create(name="Drifted Sponsor Org", representative="repuser")
        Membership.objects.create(
            target_organization=sponsor_org,
            membership_type_id="sponsor-standard",
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )
        sponsor_rep = FreeIPAUser(
            "repuser",
            {
                "uid": ["repuser"],
                "displayname": ["Representative User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "repuser":
                return sponsor_rep
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-sponsors"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Drifted Sponsor Org")

    def test_membership_stats_data_requires_permission(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer User"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "Permission denied."})

    def test_membership_stats_page_renders_chart_assets_for_authorized_user(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'src="/static/core/vendor/chartjs/chart.umd.min.js"')
        self.assertContains(resp, 'src="/static/core/js/membership_stats.js"')
        self.assertContains(resp, f'data-url="{reverse("membership-stats-data")}"')

    def test_membership_stats_page_includes_total_freeipa_users_summary_card(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-stat-key="total_freeipa_users"')

    def test_membership_stats_data_returns_expected_top_level_shape(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("summary", payload)
        self.assertIn("charts", payload)

    def test_membership_stats_data_includes_nationality_distributions(self) -> None:
        from django.core.cache import cache

        cache.clear()

        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
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

        Membership.objects.update_or_create(
            target_username="alice",
            membership_type_id="individual",
            defaults={"expires_at": timezone.now() + datetime.timedelta(days=30)},
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        country_attr = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR).strip()
        alice = FreeIPAUser("alice", {"uid": ["alice"], country_attr: ["US"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], country_attr: ["CA"], "memberof_group": []})
        carol = FreeIPAUser(
            "carol",
            {
                "uid": ["carol"],
                country_attr: ["MX"],
                "nsaccountlock": [True],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            if username == "carol":
                return carol
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[alice, bob, carol]):
                resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["summary"]["total_freeipa_users"], 3)

        charts = payload["charts"]

        all_labels = charts["nationality_all_users"]["labels"]
        all_counts = charts["nationality_all_users"]["counts"]
        # Carol is locked (inactive) and should not be counted.
        self.assertEqual(dict(zip(all_labels, all_counts, strict=False)), {"US": 1, "CA": 1})

        active_labels = charts["nationality_active_members"]["labels"]
        active_counts = charts["nationality_active_members"]["counts"]
        self.assertEqual(dict(zip(active_labels, active_counts, strict=False)), {"US": 1})
