import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_log_side_effects import resolve_term_start_at
from core.models import (
    FreeIPAPermissionGrant,
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.permissions import ASTRA_VIEW_MEMBERSHIP


class MembershipStatsDashboardTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_membership_stats_permission(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _reviewer_user(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def _create_membership_type_for_requests(self) -> None:
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

    def _create_decided_membership_request(
        self,
        *,
        username: str,
        requested_at: datetime.datetime,
        decided_at: datetime.datetime,
        status: str = MembershipRequest.Status.approved,
    ) -> None:
        request = MembershipRequest.objects.create(
            requested_username=username,
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.filter(pk=request.pk).update(
            requested_at=requested_at,
            decided_at=decided_at,
            status=status,
        )

    def _create_pending_membership_request(
        self,
        *,
        username: str,
        requested_at: datetime.datetime,
    ) -> None:
        request = MembershipRequest.objects.create(
            requested_username=username,
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.filter(pk=request.pk).update(requested_at=requested_at)

    def _create_membership_log(
        self,
        *,
        username: str,
        action: str,
        created_at: datetime.datetime,
        expires_at: datetime.datetime | None,
    ) -> None:
        log = MembershipLog.objects.create(
            actor_username="committee",
            target_username=username,
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=action,
            expires_at=expires_at,
        )
        MembershipLog.objects.filter(pk=log.pk).update(created_at=created_at, expires_at=expires_at)

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

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
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

    def test_membership_sponsors_page_uses_bulk_freeipa_lookup_for_multiple_representatives(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 0,
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

        first_org = Organization.objects.create(name="First Sponsor", representative="repone")
        second_org = Organization.objects.create(name="Second Sponsor", representative="reptwo")

        Membership.objects.create(
            target_organization=first_org,
            membership_type_id="sponsor-standard",
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )
        Membership.objects.create(
            target_organization=second_org,
            membership_type_id="sponsor-standard",
            expires_at=timezone.now() + datetime.timedelta(days=14),
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

        rep_one = FreeIPAUser(
            "repone",
            {
                "uid": ["repone"],
                "displayname": ["Representative One"],
                "memberof_group": [],
            },
        )
        rep_two = FreeIPAUser(
            "reptwo",
            {
                "uid": ["reptwo"],
                "displayname": ["Representative Two"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            raise AssertionError(f"Unexpected per-user FreeIPA lookup for representative username={username}")

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=[rep_one, rep_two]) as mocked_all:
            with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
                resp = self.client.get(reverse("membership-sponsors"))

        self.assertEqual(resp.status_code, 200)
        mocked_all.assert_called_once()
        self.assertContains(resp, "Representative One (repone)")
        self.assertContains(resp, "Representative Two (reptwo)")

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
        self.assertContains(resp, f'data-url="{reverse("membership-stats-data")}?days=365"')

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

    def test_membership_stats_page_geo_labels_disambiguate_populations(self) -> None:
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
        self.assertContains(resp, "Country Code Distribution (All Active FreeIPA Users)")
        self.assertContains(resp, "Country Code Distribution (Active Individual Members)")

    def test_membership_stats_page_forwards_days_query_to_json_data_url(self) -> None:
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"), {"days": "90"})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-url="/membership/stats/data/?days=90"')

    def test_membership_stats_page_invalid_days_defaults_to_365_in_data_url(self) -> None:
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"), {"days": "bogus"})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-url="/membership/stats/data/?days=365"')

    def test_membership_stats_data_invalid_days_returns_400(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "7"})

        self.assertEqual(resp.status_code, 400)

    def test_membership_stats_data_unknown_date_range_param_returns_400(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"start": "2020-01-01"})

        self.assertEqual(resp.status_code, 400)

    def test_membership_stats_data_default_matches_days_365(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        self._create_decided_membership_request(
            username="recent",
            requested_at=now - datetime.timedelta(days=30),
            decided_at=now - datetime.timedelta(days=28),
        )
        self._create_decided_membership_request(
            username="old",
            requested_at=now - datetime.timedelta(days=370),
            decided_at=now - datetime.timedelta(days=369),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                default_resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(default_resp.status_code, 200)

        cache.clear()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                explicit_resp = self.client.get(reverse("membership-stats-data"), {"days": "365"})

        self.assertEqual(explicit_resp.status_code, 200)
        self.assertEqual(default_resp.json()["charts"], explicit_resp.json()["charts"])

    def test_membership_stats_data_days_all_includes_all_time_trends(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        self._create_decided_membership_request(
            username="recent",
            requested_at=now - datetime.timedelta(days=10),
            decided_at=now - datetime.timedelta(days=9),
        )
        self._create_decided_membership_request(
            username="historical",
            requested_at=now - datetime.timedelta(days=500),
            decided_at=now - datetime.timedelta(days=499),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp_365 = self.client.get(reverse("membership-stats-data"), {"days": "365"})

        self.assertEqual(resp_365.status_code, 200)

        cache.clear()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp_all = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp_all.status_code, 200)

        requests_365 = int(sum(resp_365.json()["charts"]["requests_trend"]["counts"]))
        requests_all = int(sum(resp_all.json()["charts"]["requests_trend"]["counts"]))
        decisions_365 = int(
            sum(sum(dataset.get("data", [])) for dataset in resp_365.json()["charts"]["decisions_trend"]["datasets"])
        )
        decisions_all = int(
            sum(sum(dataset.get("data", [])) for dataset in resp_all.json()["charts"]["decisions_trend"]["datasets"])
        )

        self.assertEqual(requests_365, 1)
        self.assertEqual(requests_all, 2)
        self.assertEqual(decisions_365, 1)
        self.assertEqual(decisions_all, 2)

    def test_membership_stats_data_cache_key_differs_by_days_preset(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        seen_keys: list[str] = []

        def _cache_get_or_set(key: str, compute_payload, timeout: int) -> dict[str, object]:
            seen_keys.append(key)
            return compute_payload()

        with patch("core.views_membership_admin.cache.get_or_set", side_effect=_cache_get_or_set):
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
                with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                    resp_30 = self.client.get(reverse("membership-stats-data"), {"days": "30"})
                    resp_365 = self.client.get(reverse("membership-stats-data"), {"days": "365"})

        self.assertEqual(resp_30.status_code, 200)
        self.assertEqual(resp_365.status_code, 200)
        self.assertEqual(len(seen_keys), 2)
        self.assertNotEqual(seen_keys[0], seen_keys[1])

    def test_membership_stats_page_includes_approval_time_metric_placeholders(self) -> None:
        self._grant_membership_stats_permission()
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-stat-key="approval_time_mean_hours"')
        self.assertContains(resp, 'data-stat-key="approval_time_median_hours"')
        self.assertContains(resp, 'data-stat-key="approval_time_p90_hours"')

    def test_membership_stats_data_exposes_approval_time_metrics_in_whole_hours(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        self._create_decided_membership_request(
            username="hours-a",
            requested_at=now - datetime.timedelta(days=3),
            decided_at=now - datetime.timedelta(days=1),
        )
        self._create_decided_membership_request(
            username="hours-b",
            requested_at=now - datetime.timedelta(days=5),
            decided_at=now - datetime.timedelta(days=3),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        approval_metrics = resp.json()["summary"].get("approval_time")
        self.assertEqual(approval_metrics["mean_hours"], 48)
        self.assertEqual(approval_metrics["median_hours"], 48)
        self.assertEqual(approval_metrics["p90_hours"], 48)

    def test_membership_stats_approval_metrics_exclude_rows_missing_decided_at(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        self._create_decided_membership_request(
            username="decided",
            requested_at=now - datetime.timedelta(days=12),
            decided_at=now - datetime.timedelta(days=10),
        )
        self._create_pending_membership_request(
            username="pending",
            requested_at=now - datetime.timedelta(days=5),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        approval_metrics = resp.json()["summary"].get("approval_time")
        self.assertEqual(approval_metrics["sample_size"], 1)
        self.assertEqual(approval_metrics["mean_hours"], 48)

    def test_membership_stats_approval_metrics_window_uses_decided_at(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        # Requested outside the 30-day window, but decided inside it.
        self._create_decided_membership_request(
            username="inside-by-decision",
            requested_at=now - datetime.timedelta(days=120),
            decided_at=now - datetime.timedelta(days=20),
        )
        # Decided outside the 30-day window.
        self._create_decided_membership_request(
            username="outside-window",
            requested_at=now - datetime.timedelta(days=60),
            decided_at=now - datetime.timedelta(days=40),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "30"})

        self.assertEqual(resp.status_code, 200)
        approval_metrics = resp.json()["summary"].get("approval_time")
        self.assertEqual(approval_metrics["sample_size"], 1)
        self.assertEqual(approval_metrics["mean_hours"], 2400)

    def test_membership_stats_approval_metrics_exclude_outliers_above_default_cutoff(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        self._create_decided_membership_request(
            username="normal",
            requested_at=now - datetime.timedelta(days=20),
            decided_at=now - datetime.timedelta(days=10),
        )
        self._create_decided_membership_request(
            username="outlier",
            requested_at=now - datetime.timedelta(days=220),
            decided_at=now - datetime.timedelta(days=20),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "365"})

        self.assertEqual(resp.status_code, 200)
        approval_metrics = resp.json()["summary"].get("approval_time")
        self.assertEqual(approval_metrics["sample_size"], 1)
        self.assertEqual(approval_metrics["mean_hours"], 240)

    @override_settings(MEMBERSHIP_STATS_APPROVAL_OUTLIER_DAYS=30)
    def test_membership_stats_approval_metrics_outlier_cutoff_is_configurable(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        self._create_decided_membership_request(
            username="short",
            requested_at=now - datetime.timedelta(days=12),
            decided_at=now - datetime.timedelta(days=10),
        )
        self._create_decided_membership_request(
            username="long",
            requested_at=now - datetime.timedelta(days=45),
            decided_at=now - datetime.timedelta(days=2),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        approval_metrics = resp.json()["summary"].get("approval_time")
        self.assertEqual(approval_metrics["sample_size"], 1)
        self.assertEqual(approval_metrics["mean_hours"], 48)

    def test_membership_stats_retention_classifies_uninterrupted_renewal(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=500)
        first_expires_at = first_approved_at + datetime.timedelta(days=90)
        renewed_at = first_expires_at - datetime.timedelta(days=1)

        self._create_membership_log(
            username="retained-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )
        self._create_membership_log(
            username="retained-user",
            action=MembershipLog.Action.approved,
            created_at=renewed_at,
            expires_at=renewed_at + datetime.timedelta(days=90),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        cohort_index = cohorts["labels"].index(cohort_label)
        self.assertEqual(cohorts["retained"][cohort_index], 1)
        self.assertEqual(cohorts["lapsed_then_renewed"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_not_renewed"][cohort_index], 0)

    def test_membership_stats_retention_does_not_delegate_boundary_to_resolve_term_start_at(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=500)
        first_expires_at = first_approved_at + datetime.timedelta(days=90)
        renewed_at = first_expires_at + datetime.timedelta(days=2)

        self._create_membership_log(
            username="canonical-term-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )
        self._create_membership_log(
            username="canonical-term-user",
            action=MembershipLog.Action.approved,
            created_at=renewed_at,
            expires_at=renewed_at + datetime.timedelta(days=90),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                with patch(
                    "core.membership_log_side_effects.resolve_term_start_at",
                    wraps=resolve_term_start_at,
                ) as mocked_resolve:
                    resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mocked_resolve.call_count, 0)

    def test_membership_stats_retention_classifies_lapsed_then_renewed(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=520)
        first_expires_at = first_approved_at + datetime.timedelta(days=90)
        renewed_at = first_expires_at + datetime.timedelta(days=10)

        self._create_membership_log(
            username="lapsed-returned-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )
        self._create_membership_log(
            username="lapsed-returned-user",
            action=MembershipLog.Action.approved,
            created_at=renewed_at,
            expires_at=renewed_at + datetime.timedelta(days=90),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        cohort_index = cohorts["labels"].index(cohort_label)
        self.assertEqual(cohorts["retained"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_then_renewed"][cohort_index], 1)
        self.assertEqual(cohorts["lapsed_not_renewed"][cohort_index], 0)

    def test_membership_stats_retention_classifies_expired_without_renewal_as_lapsed(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=500)
        first_expires_at = first_approved_at + datetime.timedelta(days=90)

        self._create_membership_log(
            username="expired-no-renewal-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        cohort_index = cohorts["labels"].index(cohort_label)
        self.assertEqual(cohorts["retained"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_then_renewed"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_not_renewed"][cohort_index], 1)

    def test_membership_stats_retention_classifies_terminated_without_renewal_as_lapsed(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=500)
        first_expires_at = first_approved_at + datetime.timedelta(days=365)
        terminated_at = first_approved_at + datetime.timedelta(days=30)

        self._create_membership_log(
            username="terminated-no-renewal-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )
        self._create_membership_log(
            username="terminated-no-renewal-user",
            action=MembershipLog.Action.terminated,
            created_at=terminated_at,
            expires_at=None,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        cohort_index = cohorts["labels"].index(cohort_label)
        self.assertEqual(cohorts["retained"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_then_renewed"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_not_renewed"][cohort_index], 1)

    def test_membership_stats_retention_approval_on_expiry_boundary_is_retained(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=500)
        first_expires_at = first_approved_at + datetime.timedelta(days=90)

        self._create_membership_log(
            username="expiry-boundary-retained-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )
        self._create_membership_log(
            username="expiry-boundary-retained-user",
            action=MembershipLog.Action.approved,
            created_at=first_expires_at,
            expires_at=first_expires_at + datetime.timedelta(days=90),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        cohort_index = cohorts["labels"].index(cohort_label)
        self.assertEqual(cohorts["retained"][cohort_index], 1)
        self.assertEqual(cohorts["lapsed_then_renewed"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_not_renewed"][cohort_index], 0)

    def test_membership_stats_retention_approval_after_expiry_is_lapsed_then_renewed(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        now = timezone.now()
        first_approved_at = now - datetime.timedelta(days=500)
        first_expires_at = first_approved_at + datetime.timedelta(days=90)
        renewed_at = first_expires_at + datetime.timedelta(days=1)

        self._create_membership_log(
            username="expiry-plus-one-user",
            action=MembershipLog.Action.approved,
            created_at=first_approved_at,
            expires_at=first_expires_at,
        )
        self._create_membership_log(
            username="expiry-plus-one-user",
            action=MembershipLog.Action.approved,
            created_at=renewed_at,
            expires_at=renewed_at + datetime.timedelta(days=90),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        cohort_index = cohorts["labels"].index(cohort_label)
        self.assertEqual(cohorts["retained"][cohort_index], 0)
        self.assertEqual(cohorts["lapsed_then_renewed"][cohort_index], 1)
        self.assertEqual(cohorts["lapsed_not_renewed"][cohort_index], 0)

    def test_membership_stats_retention_empty_window_has_no_data(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        summary = resp.json()["summary"]["retention_cohort_12m"]
        self.assertEqual(summary["cohorts"], 0)
        self.assertEqual(summary["users"], 0)

        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        self.assertEqual(cohorts["labels"], [])
        self.assertEqual(cohorts["retained"], [])
        self.assertEqual(cohorts["lapsed_then_renewed"], [])
        self.assertEqual(cohorts["lapsed_not_renewed"], [])

    def test_membership_stats_retention_limits_to_last_12_cohort_months(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        base = timezone.now().astimezone(datetime.UTC).replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        for idx in range(13):
            approved_at = base - datetime.timedelta(days=(idx + 13) * 31)
            self._create_membership_log(
                username=f"cohort-user-{idx}",
                action=MembershipLog.Action.approved,
                created_at=approved_at,
                expires_at=approved_at + datetime.timedelta(days=60),
            )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"), {"days": "all"})

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        self.assertEqual(len(cohorts["labels"]), 12)

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

    def test_membership_stats_expirations_chart_excludes_expiry_exactly_at_now(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)

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

        Membership.objects.create(
            target_username="boundary-now",
            membership_type_id="individual",
            expires_at=frozen_now,
        )
        Membership.objects.create(
            target_username="future",
            membership_type_id="individual",
            expires_at=frozen_now + datetime.timedelta(days=1),
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

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
                with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                    resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        expirations_counts = list(payload["charts"]["expirations_upcoming"]["counts"])
        self.assertEqual(sum(expirations_counts), 1)

    def test_membership_stats_data_includes_nationality_distributions(self) -> None:
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

    def test_membership_stats_data_deduplicates_mirror_plus_other_memberships_per_target(self) -> None:
        cache.clear()
        Membership.objects.all().delete()

        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            name="mirror",
            defaults={
                "is_individual": True,
                "is_organization": True,
                "sort_order": 1,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 2,
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
            code="sponsor-standard",
            defaults={
                "name": "Sponsor Standard",
                "group_cn": "almalinux-sponsor-standard",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="mirror",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        org = Organization.objects.create(name="Dual Membership Org", representative="orgrep")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="mirror",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )
        Membership.objects.create(
            target_organization=org,
            membership_type_id="sponsor-standard",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_membership_stats_permission()
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 200)
        summary = resp.json()["summary"]
        self.assertEqual(summary["active_individual_memberships"], 1, summary)
        self.assertEqual(summary["active_org_sponsorships"], 1, summary)

    def test_membership_stats_active_members_geo_uses_individual_population_only(self) -> None:
        cache.clear()

        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            name="committee",
            defaults={
                "is_individual": False,
                "is_organization": False,
                "sort_order": 1,
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
        MembershipType.objects.update_or_create(
            code="committee",
            defaults={
                "name": "Committee",
                "group_cn": "almalinux-committee",
                "category_id": "committee",
                "sort_order": 1,
                "enabled": True,
            },
        )

        Membership.objects.update_or_create(
            target_username="alice",
            membership_type_id="individual",
            defaults={"expires_at": timezone.now() + datetime.timedelta(days=30)},
        )
        Membership.objects.update_or_create(
            target_username="bob",
            membership_type_id="committee",
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

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[alice, bob]):
                resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        active = payload["charts"]["nationality_active_members"]
        self.assertEqual(dict(zip(active["labels"], active["counts"], strict=False)), {"US": 1})

    def test_membership_stats_geo_groups_invalid_and_unset_country_codes(self) -> None:
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
            target_username="badcode",
            membership_type_id="individual",
            defaults={"expires_at": timezone.now() + datetime.timedelta(days=30)},
        )
        Membership.objects.update_or_create(
            target_username="unset",
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
        badcode = FreeIPAUser("badcode", {"uid": ["badcode"], country_attr: ["XX"], "memberof_group": []})
        unset = FreeIPAUser("unset", {"uid": ["unset"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "badcode":
                return badcode
            if username == "unset":
                return unset
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[badcode, unset]):
                resp = self.client.get(reverse("membership-stats-data"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        all_users = payload["charts"]["nationality_all_users"]
        all_counts = dict(zip(all_users["labels"], all_users["counts"], strict=False))
        self.assertEqual(all_counts.get("Unknown/Unset"), 2)

        active_members = payload["charts"]["nationality_active_members"]
        active_counts = dict(zip(active_members["labels"], active_members["counts"], strict=False))
        self.assertEqual(active_counts.get("Unknown/Unset"), 2)
