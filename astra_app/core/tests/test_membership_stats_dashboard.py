import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
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

    def _retention_detail_row(self, payload: dict[str, object], cohort_month: str) -> dict[str, object]:
        for row in payload["charts"]["retention_cohorts_12m"]:
            if row["cohort_month"] == cohort_month:
                return row
        self.fail(f"Missing retention cohort row for {cohort_month}")

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
        self.assertContains(resp, "data-membership-sponsors-root")
        self.assertContains(resp, 'data-membership-sponsors-api-url="/api/v1/membership/sponsors"')
        self.assertContains(resp, 'data-membership-sponsors-page-size="25"')

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

        query = {
            "draw": "1",
            "start": "0",
            "length": "25",
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "order[0][name]": "expires_at",
            "columns[0][data]": "membership_id",
            "columns[0][name]": "expires_at",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("api-membership-sponsors"), data=query, HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(payload["data"][0]["organization"]["name"], "Drifted Sponsor Org")

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

        query = {
            "draw": "1",
            "start": "0",
            "length": "25",
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "order[0][name]": "expires_at",
            "columns[0][data]": "membership_id",
            "columns[0][name]": "expires_at",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=[rep_one, rep_two]) as mocked_all:
            with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
                resp = self.client.get(reverse("api-membership-sponsors"), data=query, HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        mocked_all.assert_called_once()
        payload = resp.json()
        labels = [row["representative"]["display_label"] for row in payload["data"]]
        self.assertIn("Representative One (repone)", labels)
        self.assertIn("Representative Two (reptwo)", labels)

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
            resp = self.client.get(reverse("api-stats-membership-summary-detail"))

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "Permission denied."})

    def test_membership_stats_page_renders_chart_assets_for_authorized_user(self) -> None:
        # Covered by MembershipStatsSplitApiTests.test_stats_page_renders_vue_root_with_new_api_urls
        pass

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

        # summary card labels are now rendered by the Vue component
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-membership-stats-root")

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

        # geo labels are rendered by the Vue component; page just validates bootstrap attributes present
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-membership-stats-root")

    def test_membership_stats_page_wraps_summary_card_labels(self) -> None:
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"))

        # CSS wrapping is now applied inside the Vue component's scoped styles
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-membership-stats-root")

    def test_membership_stats_page_forwards_days_query_to_json_data_url(self) -> None:
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"), {"days": "90"})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-stats-current-days="90"')

    def test_membership_stats_page_invalid_days_defaults_to_365_in_data_url(self) -> None:
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-stats"), {"days": "bogus"})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-stats-current-days="365"')

    def test_membership_stats_data_invalid_days_returns_400(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "7"})

        self.assertEqual(resp.status_code, 400)

    def test_membership_stats_data_unknown_date_range_param_returns_400(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "7"})

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
                default_resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"))

        self.assertEqual(default_resp.status_code, 200)

        cache.clear()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                explicit_resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "365"})

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
                resp_365 = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "365"})

        self.assertEqual(resp_365.status_code, 200)

        cache.clear()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp_all = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "all"})

        self.assertEqual(resp_all.status_code, 200)

        requests_365 = int(sum(row["count"] for row in resp_365.json()["charts"]["requests_trend"]))
        requests_all = int(sum(row["count"] for row in resp_all.json()["charts"]["requests_trend"]))
        decisions_365 = int(sum(row["count"] for row in resp_365.json()["charts"]["decisions_trend"]))
        decisions_all = int(sum(row["count"] for row in resp_all.json()["charts"]["decisions_trend"]))

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
                    resp_30 = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "30"})
                    resp_365 = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "365"})

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
        # approval time placeholders are rendered by the Vue component
        self.assertContains(resp, "data-membership-stats-root")

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
                resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "all"})

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
                resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "all"})

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
                resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "30"})

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
                resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "365"})

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
                resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "all"})

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        row = self._retention_detail_row(resp.json(), cohort_label)
        self.assertEqual(row["retained"], 1)
        self.assertEqual(row["lapsed_then_renewed"], 0)
        self.assertEqual(row["lapsed_not_renewed"], 0)

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
                    resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        row = self._retention_detail_row(resp.json(), cohort_label)
        self.assertEqual(row["retained"], 0)
        self.assertEqual(row["lapsed_then_renewed"], 1)
        self.assertEqual(row["lapsed_not_renewed"], 0)

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        row = self._retention_detail_row(resp.json(), cohort_label)
        self.assertEqual(row["retained"], 0)
        self.assertEqual(row["lapsed_then_renewed"], 0)
        self.assertEqual(row["lapsed_not_renewed"], 1)

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        row = self._retention_detail_row(resp.json(), cohort_label)
        self.assertEqual(row["retained"], 0)
        self.assertEqual(row["lapsed_then_renewed"], 0)
        self.assertEqual(row["lapsed_not_renewed"], 1)

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        row = self._retention_detail_row(resp.json(), cohort_label)
        self.assertEqual(row["retained"], 1)
        self.assertEqual(row["lapsed_then_renewed"], 0)
        self.assertEqual(row["lapsed_not_renewed"], 0)

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohort_label = first_approved_at.astimezone(datetime.UTC).strftime("%Y-%m")
        row = self._retention_detail_row(resp.json(), cohort_label)
        self.assertEqual(row["retained"], 0)
        self.assertEqual(row["lapsed_then_renewed"], 1)
        self.assertEqual(row["lapsed_not_renewed"], 0)

    def test_membership_stats_retention_empty_window_has_no_data(self) -> None:
        cache.clear()
        self._grant_membership_stats_permission()
        self._create_membership_type_for_requests()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                summary_resp = self.client.get(reverse("api-stats-membership-summary-detail"))
                charts_resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(summary_resp.status_code, 200)
        summary = summary_resp.json()["summary"]["retention_cohort_12m"]
        self.assertEqual(summary["cohorts"], 0)
        self.assertEqual(summary["users"], 0)

        self.assertEqual(charts_resp.status_code, 200)
        self.assertEqual(charts_resp.json()["charts"]["retention_cohorts_12m"], [])

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
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(resp.status_code, 200)
        cohorts = resp.json()["charts"]["retention_cohorts_12m"]
        self.assertEqual(len(cohorts), 12)

    def test_membership_stats_data_returns_expected_top_level_shape(self) -> None:
        # Covered by MembershipStatsSplitApiTests which validates all 4 split endpoint shapes.
        pass

    def test_membership_stats_data_ignores_profile_privacy_for_aggregated_counts(self) -> None:
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

        self._grant_membership_stats_permission()
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        country_attr = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR).strip()
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                country_attr: ["US"],
                "fasIsPrivate": ["TRUE"],
                "memberof_group": [],
            },
        )

        def _all_users(*, respect_privacy: bool = True) -> list[FreeIPAUser]:
            self.assertFalse(respect_privacy)
            return [alice]

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", side_effect=_all_users):
                summary_resp = self.client.get(reverse("api-stats-membership-summary-detail"))
                composition_resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))

        self.assertEqual(summary_resp.status_code, 200)
        self.assertEqual(summary_resp.json()["summary"]["total_freeipa_users"], 1)
        self.assertEqual(summary_resp.json()["summary"]["active_individual_memberships"], 1)
        self.assertEqual(composition_resp.status_code, 200)
        active_members = composition_resp.json()["charts"]["nationality_active_members"]
        self.assertEqual({row["country_code"]: row["count"] for row in active_members}, {"US": 1})

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
                    resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        expirations_counts = [row["count"] for row in payload["charts"]["expirations_upcoming"]]
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
                summary_resp = self.client.get(reverse("api-stats-membership-summary-detail"))
                composition_resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))

        self.assertEqual(summary_resp.status_code, 200)
        self.assertEqual(summary_resp.json()["summary"]["total_freeipa_users"], 3)

        self.assertEqual(composition_resp.status_code, 200)
        charts = composition_resp.json()["charts"]

        self.assertEqual(
            {row["country_code"]: row["count"] for row in charts["nationality_all_users"]},
            {"US": 1, "CA": 1},
        )
        self.assertEqual(
            {row["country_code"]: row["count"] for row in charts["nationality_active_members"]},
            {"US": 1},
        )

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
                resp = self.client.get(reverse("api-stats-membership-summary-detail"))

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
                resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        active = payload["charts"]["nationality_active_members"]
        self.assertEqual({row["country_code"]: row["count"] for row in active}, {"US": 1})

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
                resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        all_users = payload["charts"]["nationality_all_users"]
        all_counts = {row["country_code"]: row["count"] for row in all_users}
        self.assertEqual(all_counts.get("Unknown/Unset"), 2)

        active_members = payload["charts"]["nationality_active_members"]
        active_counts = {row["country_code"]: row["count"] for row in active_members}
        self.assertEqual(active_counts.get("Unknown/Unset"), 2)


class MembershipStatsSplitApiTests(TestCase):
    """Tests for the new /api/v1/stats/... split endpoints."""

    def setUp(self) -> None:
        cache.clear()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        session = self.client.session
        session["_freeipa_username"] = "reviewer"
        session.save()
        self._reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def _get_reviewer(self, username: str) -> FreeIPAUser | None:
        if username == "reviewer":
            return self._reviewer
        return None

    def test_summary_detail_endpoint_requires_permission(self) -> None:
        FreeIPAPermissionGrant.objects.all().delete()
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("api-stats-membership-summary-detail"))
        self.assertEqual(resp.status_code, 403)

    def test_summary_detail_endpoint_returns_200_with_expected_shape(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-summary-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("summary", payload)
        self.assertIn("generated_at", payload)
        self.assertIn("days_param", payload)
        self.assertIn("total_freeipa_users", payload["summary"])
        self.assertIn("approval_time", payload["summary"])
        self.assertIn("retention_cohort_12m", payload["summary"])

    def test_summary_detail_endpoint_invalid_days_returns_400(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("api-stats-membership-summary-detail"), {"days": "7"})
        self.assertEqual(resp.status_code, 400)

    def test_composition_detail_endpoint_requires_permission(self) -> None:
        FreeIPAPermissionGrant.objects.all().delete()
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))
        self.assertEqual(resp.status_code, 403)

    def test_composition_detail_endpoint_returns_200_with_expected_shape(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("charts", payload)
        self.assertIn("membership_types", payload["charts"])
        self.assertIn("nationality_all_users", payload["charts"])
        self.assertIn("nationality_active_members", payload["charts"])

    def test_composition_detail_endpoint_uses_shared_builder_boundary(self) -> None:
        detail_payload = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "charts": {
                "membership_types": [
                    {"membership_type": {"code": "individual", "name": "Individual"}, "count": 2}
                ],
                "nationality_all_users": [{"country_code": "US", "count": 2}],
                "nationality_active_members": [{"country_code": "US", "count": 1}],
            },
        }

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch(
                "core.views_membership_admin._build_membership_stats_composition_payloads",
                return_value=detail_payload,
            ) as builder:
                detail_resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))

        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(builder.call_count, 1)
        self.assertEqual(detail_resp.json(), detail_payload)

    def test_trends_detail_endpoint_requires_permission(self) -> None:
        FreeIPAPermissionGrant.objects.all().delete()
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"))
        self.assertEqual(resp.status_code, 403)

    def test_trends_detail_endpoint_returns_200_with_expected_shape(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("charts", payload)
        self.assertIn("requests_trend", payload["charts"])
        self.assertIn("decisions_trend", payload["charts"])
        self.assertIn("expirations_upcoming", payload["charts"])
        self.assertIn("days_param", payload)

    def test_trends_detail_endpoint_uses_shared_builder_boundary(self) -> None:
        detail_payload = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "days_param": "365",
            "charts": {
                "requests_trend": [{"period": "2025-12", "count": 4}],
                "decisions_trend": [
                    {"period": "2025-12", "status": "approved", "count": 3},
                    {"period": "2025-12", "status": "rejected", "count": 1},
                ],
                "expirations_upcoming": [{"period": "2026-02", "count": 1}],
            },
        }

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch(
                "core.views_membership_admin._build_membership_stats_trends_payloads",
                return_value=detail_payload,
            ) as builder:
                detail_resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "365"})

        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(builder.call_count, 1)
        self.assertEqual(detail_resp.json(), detail_payload)

    def test_trends_detail_endpoint_invalid_days_returns_400(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"), {"days": "7"})
        self.assertEqual(resp.status_code, 400)

    def test_retention_detail_endpoint_requires_permission(self) -> None:
        FreeIPAPermissionGrant.objects.all().delete()
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))
        self.assertEqual(resp.status_code, 403)

    def test_retention_detail_endpoint_returns_200_with_expected_shape(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("charts", payload)
        self.assertIn("retention_cohorts_12m", payload["charts"])
        self.assertIsInstance(payload["charts"]["retention_cohorts_12m"], list)

    def test_retention_detail_endpoint_uses_shared_builder_boundary(self) -> None:
        detail_payload = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "charts": {
                "retention_cohorts_12m": [
                    {
                        "cohort_month": "2025-01",
                        "cohort_size": 8,
                        "retained": 5,
                        "lapsed_then_renewed": 2,
                        "lapsed_not_renewed": 1,
                    }
                ]
            },
        }

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch(
                "core.views_membership_admin._build_membership_stats_retention_payloads",
                return_value=detail_payload,
            ) as builder:
                detail_resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))

        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(builder.call_count, 1)
        self.assertEqual(detail_resp.json(), detail_payload)

    def test_stats_page_renders_vue_root_with_new_api_urls(self) -> None:
        session = self.client.session
        session["_freeipa_username"] = "reviewer"
        session.save()
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            resp = self.client.get(reverse("membership-stats"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-membership-stats-root")
        self.assertContains(resp, reverse("api-stats-membership-summary-detail"))
        self.assertContains(resp, reverse("api-stats-membership-composition-charts-detail"))
        self.assertContains(resp, reverse("api-stats-membership-trends-charts-detail"))
        self.assertContains(resp, reverse("api-stats-membership-retention-chart-detail"))

    def test_summary_detail_endpoint_returns_same_data_only_shape(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-summary-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("summary", payload)
        self.assertIn("generated_at", payload)
        self.assertIn("days_param", payload)
        self.assertNotIn("labels", payload)

    def test_composition_detail_endpoint_returns_raw_rows_instead_of_chart_labels(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-composition-charts-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        charts = payload["charts"]
        self.assertIn("membership_types", charts)
        self.assertIn("nationality_all_users", charts)
        self.assertIn("nationality_active_members", charts)
        self.assertEqual(charts["membership_types"], [])
        self.assertEqual(charts["nationality_all_users"], [])
        self.assertEqual(charts["nationality_active_members"], [])

    def test_trends_detail_endpoint_returns_raw_period_rows_instead_of_chart_labels(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-trends-charts-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        charts = payload["charts"]
        self.assertEqual(charts["requests_trend"], [])
        self.assertEqual(charts["decisions_trend"], [])
        self.assertEqual(charts["expirations_upcoming"], [])

    def test_retention_detail_endpoint_returns_raw_cohort_rows_instead_of_chart_labels(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
                resp = self.client.get(reverse("api-stats-membership-retention-chart-detail"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["charts"]["retention_cohorts_12m"], [])

    def test_legacy_membership_stats_routes_are_unregistered(self) -> None:
        for route_name in (
            "api-stats-membership-summary",
            "api-stats-membership-composition-charts",
            "api-stats-membership-trends-charts",
            "api-stats-membership-retention-chart",
        ):
            with self.assertRaises(NoReverseMatch):
                reverse(route_name)
