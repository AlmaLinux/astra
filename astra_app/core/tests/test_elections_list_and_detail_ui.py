
import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    ExclusionGroup,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.ballot_chain import compute_chain_hash
from core.tests.utils_test_data import ensure_core_categories
from core.tokens import election_genesis_chain_hash


class ElectionsListDraftVisibilityTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_elections_list_hides_drafts_for_non_managers(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        open_election = Election.objects.create(
            name="Published election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Election.objects.create(
            name="Draft election",
            description="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("elections"))
            api_resp = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-root')
        self.assertContains(resp, reverse("api-elections"))
        self.assertNotContains(resp, "Draft election")
        self.assertNotContains(resp, "Published election")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertEqual([item["name"] for item in payload["items"]], ["Published election"])
        self.assertEqual(payload["items"][0]["detail_url"], reverse("election-detail", args=[open_election.id]))

    def test_elections_list_shows_drafts_for_managers_and_links_to_edit(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        open_election = Election.objects.create(
            name="Published election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        draft_election = Election.objects.create(
            name="Draft election",
            description="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _get_user(username: str):
            if username == "admin":
                return admin
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("elections"))
            api_resp = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-root')
        self.assertContains(resp, reverse("election-edit", args=[0]))
        self.assertNotContains(resp, "Draft election")
        self.assertNotContains(resp, "Published election")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        names = [item["name"] for item in payload["items"]]
        self.assertEqual(names, ["Draft election", "Published election"])
        self.assertEqual(payload["items"][0]["edit_url"], reverse("election-edit", args=[draft_election.id]))
        self.assertEqual(payload["items"][1]["detail_url"], reverse("election-detail", args=[open_election.id]))


class ElectionsListGroupingTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_elections_list_splits_open_and_past_elections(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        Election.objects.create(
            name="Open election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        past_election = Election.objects.create(
            name="Past election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=9),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections"))
            api_resp = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-root')
        self.assertNotContains(resp, "Open election")
        self.assertNotContains(resp, "Past election")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertEqual([item["name"] for item in payload["items"]], ["Open election", "Past election"])
        self.assertEqual(payload["items"][1]["detail_url"], reverse("election-detail", args=[past_election.id]))


class ElectionsDeletedVisibilityTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_elections_list_hides_deleted_for_non_managers(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        Election.objects.create(
            name="Visible election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        deleted = Election.objects.create(
            name="Deleted election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=9),
            number_of_seats=1,
            status="deleted",
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections"))
            api_resp = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-root')
        self.assertNotContains(resp, "Visible election")
        self.assertNotContains(resp, "Deleted election")
        self.assertNotContains(resp, reverse("election-detail", args=[deleted.id]))

        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual([item["name"] for item in api_resp.json()["items"]], ["Visible election"])

    def test_elections_list_hides_deleted_for_managers(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        Election.objects.create(
            name="Visible election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        deleted = Election.objects.create(
            name="Deleted election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=9),
            number_of_seats=1,
            status="deleted",
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.get(reverse("elections"))
            api_resp = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-root')
        self.assertNotContains(resp, "Visible election")
        self.assertNotContains(resp, "Deleted election")
        self.assertNotContains(resp, reverse("election-edit", args=[deleted.id]))

        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual([item["name"] for item in api_resp.json()["items"]], ["Visible election"])

    def test_election_detail_returns_404_for_deleted(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        deleted = Election.objects.create(
            name="Deleted election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=9),
            number_of_seats=1,
            status="deleted",
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[deleted.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionDetailManagerUIStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def test_turnout_progress_bars_visible_only_to_managers(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Turnout election",
            description="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=2,
            category_id="individual",
            enabled=True,
        )
        m1 = Membership.objects.create(
            target_username="voter1",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        m2 = Membership.objects.create(
            target_username="voter2",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m1.pk).update(created_at=now - datetime.timedelta(days=10))
        Membership.objects.filter(pk=m2.pk).update(created_at=now - datetime.timedelta(days=10))

        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[],
            weight=2,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[],
            weight=2,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        # Non-manager
        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Participation so far")

        # Manager
        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _get_user(username: str):
            if username == "admin":
                return admin
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("api-election-detail-info", args=[election.id]))

        # ChartJS is still loaded for the Vue turnout timeline, but legacy static hooks are gone.
        self.assertContains(resp, 'src="/static/core/vendor/chartjs/chart.umd.min.js"')
        self.assertNotContains(resp, "election_turnout_chart.js", html=False)
        self.assertNotContains(resp, "cdn.jsdelivr.net/npm/chart.js")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            api_resp = self.client.get(reverse("api-election-detail-info", args=[election.id]))
        self.assertEqual(api_resp.status_code, 200)
        election_payload = api_resp.json()["election"]
        self.assertTrue(election_payload["show_turnout_chart"])
        self.assertEqual(election_payload["turnout_stats"]["participating_voter_count"], 1)
        self.assertEqual(election_payload["turnout_stats"]["participating_vote_weight_total"], 2)
        self.assertEqual(set(election_payload["turnout_chart_data"]), {"labels", "counts"})

    def test_election_voting_window_renders_in_users_timezone(self) -> None:
        # If the user has a FreeIPA timezone configured, our middleware activates it.
        # The API should therefore provide display datetimes in that timezone for Vue.
        start_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 0, 0), timezone=timezone.UTC)
        end_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 14, 0, 0), timezone=timezone.UTC)

        election = Election.objects.create(
            name="TZ election",
            description="",
            start_datetime=start_utc,
            end_datetime=end_utc,
            number_of_seats=1,
            status=Election.Status.open,
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "memberof_group": [],
                "fasTimezone": "Europe/Paris",
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-detail-info", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("api-election-detail-info", args=[election.id]))

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()["election"]
        # 12:00Z -> 13:00 Europe/Paris (winter), 14:00Z -> 15:00.
        self.assertIn("2026-01-02 13:00", payload["start_datetime_display"])
        self.assertIn("2026-01-02 15:00", payload["end_datetime_display"])

    def test_turnout_chart_includes_zero_days_since_start(self) -> None:
        today = datetime.date(2026, 1, 2)
        now = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2025, 12, 30, 9, 0, 0))

        election = Election.objects.create(
            name="Turnout chart gaps",
            description="",
            start_datetime=start_dt,
            end_datetime=now + datetime.timedelta(days=10),
            number_of_seats=1,
            status=Election.Status.open,
        )

        # Minimal eligible voters so turnout widget renders.
        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        m = Membership.objects.create(
            target_username="voter1",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=start_dt - datetime.timedelta(days=10))

        # Create ballot_submitted audit rows on only some days.
        e0 = AuditLogEntry.objects.create(election=election, event_type="ballot_submitted", payload={}, is_public=False)
        e1 = AuditLogEntry.objects.create(election=election, event_type="ballot_submitted", payload={}, is_public=False)
        # 2025-12-30: 1
        AuditLogEntry.objects.filter(pk=e0.pk).update(timestamp=start_dt)
        # 2026-01-01: 1
        AuditLogEntry.objects.filter(pk=e1.pk).update(timestamp=timezone.make_aware(datetime.datetime(2026, 1, 1, 8, 0, 0)))

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _localdate_side_effect(dt: datetime.datetime | None = None) -> datetime.date:
            if dt is None:
                return today
            return timezone.localtime(dt).date()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
            patch("core.views_elections.detail.timezone.localdate", side_effect=_localdate_side_effect),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
            patch("core.views_elections.detail.timezone.localdate", side_effect=_localdate_side_effect),
        ):
            api_resp = self.client.get(reverse("api-election-detail-info", args=[election.id]))
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()["election"]["turnout_chart_data"]

        self.assertEqual(
            payload.get("labels"),
            ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"],
        )
        self.assertEqual(payload.get("counts"), [1, 0, 1, 0])

    def test_turnout_chart_uses_election_end_day_after_close(self) -> None:
        # When an election is closed, the votes-per-day chart should extend only
        # through election.end_datetime (not through "today").
        today = datetime.date(2026, 1, 2)
        now = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2025, 12, 30, 9, 0, 0))
        end_dt = timezone.make_aware(datetime.datetime(2026, 1, 1, 10, 0, 0))

        election = Election.objects.create(
            name="Turnout chart closed",
            description="",
            start_datetime=start_dt,
            end_datetime=end_dt,
            number_of_seats=1,
            status=Election.Status.closed,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        m = Membership.objects.create(
            target_username="voter1",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=start_dt - datetime.timedelta(days=10))

        e0 = AuditLogEntry.objects.create(election=election, event_type="ballot_submitted", payload={}, is_public=False)
        # 2025-12-30
        AuditLogEntry.objects.filter(pk=e0.pk).update(timestamp=start_dt)

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _localdate_side_effect(dt: datetime.datetime | None = None) -> datetime.date:
            if dt is None:
                return today
            return timezone.localtime(dt).date()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
            patch("core.views_elections.detail.timezone.localdate", side_effect=_localdate_side_effect),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
            patch("core.views_elections.detail.timezone.localdate", side_effect=_localdate_side_effect),
        ):
            api_resp = self.client.get(reverse("api-election-detail-info", args=[election.id]))
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()["election"]["turnout_chart_data"]

        # The chart should end at 2026-01-01 (election end), not 2026-01-02 (today).
        self.assertEqual(payload.get("labels"), ["2025-12-30", "2025-12-31", "2026-01-01"])
        self.assertEqual(payload.get("counts"), [1, 0, 0])

    def test_turnout_chart_renders_for_tallied_election_for_manager(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Turnout tallied",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"elected": []},
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        m = Membership.objects.create(
            target_username="voter1",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=365))

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'src="/static/core/vendor/chartjs/chart.umd.min.js"')
        self.assertNotContains(resp, "election_turnout_chart.js", html=False)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            api_resp = self.client.get(reverse("api-election-detail-info", args=[election.id]))
        self.assertEqual(api_resp.status_code, 200)
        self.assertTrue(api_resp.json()["election"]["show_turnout_chart"])

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
    def test_ineligible_voters_render_with_modal_details_for_manager(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Ineligible voter UI election",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        m = Membership.objects.create(
            target_username="bob",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        # Term start too recent for a 30-day minimum at election start.
        Membership.objects.filter(pk=m.pk).update(created_at=now)

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [
            FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []}),
            FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
            api_resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-election-eligible-voters-root')
        self.assertContains(resp, reverse("api-election-detail-eligible-voters", args=[election.id]))
        self.assertContains(resp, reverse("api-election-detail-ineligible-voters", args=[election.id]))
        self.assertNotContains(resp, "bob")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertIn("bob", [item["username"] for item in payload["ineligible_voters"]["items"]])

        details = payload["ineligible_voters"]["details_by_username"]["bob"]
        self.assertEqual(details["reason"], "too_new")
        self.assertEqual(details["term_start_date"], "2026-02-01")
        self.assertEqual(details["election_start_date"], "2026-02-10")

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
    def test_ineligible_voter_modal_preserves_zero_days_at_start(self) -> None:
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Zero days election",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        m = Membership.objects.create(
            target_username="bob",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=start_dt)

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [
            FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []}),
            FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
            api_resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-election-eligible-voters-root')
        self.assertNotContains(resp, 'data-ineligible-voter-details-json-id="ineligible-voter-details"')

        self.assertEqual(api_resp.status_code, 200)
        details = api_resp.json()["ineligible_voters"]["details_by_username"]["bob"]
        self.assertEqual(details["days_at_start"], 0)
        self.assertEqual(details["days_short"], 30)

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
    def test_ineligible_voters_card_is_visible_and_renders_when_empty_for_manager(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Eligible only election",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        m = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        # Eligible: membership term start is old enough.
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=365))

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
            api_resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-election-eligible-voters-root')
        self.assertNotContains(resp, "No ineligible voters found.")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertEqual(payload["ineligible_voters"]["items"], [])
        self.assertEqual(payload["ineligible_voters"]["pagination"]["count"], 0)

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
    def test_ineligible_voters_username_search_filters_before_pagination(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Ineligible voter search election",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        bob = Membership.objects.create(
            target_username="bob",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        alice = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk__in=[bob.pk, alice.pk]).update(created_at=now)

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [
            FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []}),
            FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
        ):
            resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                {"q": "bo"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual([item["username"] for item in payload["ineligible_voters"]["items"]], ["bob"])

    def test_eligible_voters_username_search_filters_grid(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Eligible voter search election",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        alice = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        bob = Membership.objects.create(
            target_username="bob",
            membership_type=mt,
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk__in=[alice.pk, bob.pk]).update(created_at=now - datetime.timedelta(days=365))

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [
            FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []}),
            FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
        ):
            resp = self.client.get(
                reverse("election-detail", args=[election.id]),
                {"eligible_q": "ali"},
            )
            api_resp = self.client.get(
                reverse("api-election-detail-eligible-voters", args=[election.id]),
                {"q": "ali"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-election-eligible-voters-root')
        self.assertContains(resp, 'data-election-eligible-voters-api-url="/api/v1/elections/')
        self.assertContains(resp, 'data-election-ineligible-voters-api-url="/api/v1/elections/')

        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual([item["username"] for item in api_resp.json()["eligible_voters"]["items"]], ["alice"])

    def test_ineligible_voters_include_group_member_with_no_membership(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Ineligible no-membership electorate",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
            eligible_group_cn="election-electorate",
        )

        # Ensure there is at least one configured vote-bearing membership type.
        MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch(
                "core.elections_eligibility._freeipa_group_recursive_member_usernames",
                return_value={"nomember"},
            ),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
            patch("core.views_elections.detail.timezone.now", return_value=now),
        ):
            resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["ineligible_voters"]
        self.assertEqual([item["username"] for item in payload["items"]], ["nomember"])
        self.assertEqual(payload["details_by_username"]["nomember"]["reason"], "no_membership")

    def test_ineligible_voters_include_freeipa_user_with_no_membership_when_no_group_cn(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Ineligible FreeIPA electorate",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
            eligible_group_cn="",
        )

        # Ensure there is at least one configured vote-bearing membership type.
        MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [FreeIPAUser("nomember", {"uid": ["nomember"], "memberof_group": []})]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
            patch("core.views_elections.detail.timezone.now", return_value=now),
        ):
            resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["ineligible_voters"]
        self.assertEqual([item["username"] for item in payload["items"]], ["nomember"])
        self.assertEqual(payload["details_by_username"]["nomember"]["reason"], "no_membership")

    def test_ineligible_voters_include_expired_membership_reason_expired(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))

        election = Election.objects.create(
            name="Ineligible expired membership electorate",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        m = Membership.objects.create(
            target_username="expireduser",
            membership_type=mt,
            expires_at=start_dt - datetime.timedelta(days=1),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=365))

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [FreeIPAUser("expireduser", {"uid": ["expireduser"], "memberof_group": []})]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
        ):
            resp = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["ineligible_voters"]
        self.assertEqual([item["username"] for item in payload["items"]], ["expireduser"])
        self.assertEqual(payload["details_by_username"]["expireduser"]["reason"], "expired")

    def test_exclusion_group_warning_renders_when_groups_exist(self) -> None:
        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")

        now = timezone.now()
        election = Election.objects.create(
            name="Exclusion election",
            description="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            description="",
            url="",
        )
        c2 = Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by="nominator",
            description="",
            url="",
        )
        group = ExclusionGroup.objects.create(
            election=election,
            name="Employees of X",
            max_elected=1,
        )
        group.candidates.add(c1, c2)

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _get_user(username: str):
            if username == "admin":
                return admin
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-detail-info", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("api-election-detail-info", args=[election.id]))

        self.assertEqual(api_resp.status_code, 200)
        messages = api_resp.json()["election"]["exclusion_group_messages"]
        self.assertEqual(len(messages), 1)
        self.assertIn("Employees of X", messages[0])
        self.assertIn("exclusion group", messages[0])
        self.assertIn("only 1 candidate", messages[0])


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionDetailConcludeElectionTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def test_conclude_button_visible_only_to_managers(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Conclude election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Conclude Election")

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _get_user(username: str):
            if username == "admin":
                return admin
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-election-conclude-action-root")
        self.assertContains(resp, reverse("api-election-conclude", args=[election.id]))
        self.assertNotContains(resp, "Election ID")
        self.assertNotContains(resp, "_modal_name_confirm.html")
        self.assertNotContains(resp, "bindNameConfirm")

    def test_conclude_post_closes_and_tallies_by_default(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Conclude election - tally",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-x",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-x",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.post(reverse("election-conclude", args=[election.id]), data={"confirm": election.name})
        self.assertEqual(resp.status_code, 302)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertTrue(election.tally_result)

    def test_conclude_post_close_only_when_checkbox_set(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Conclude election - close only",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.post(
                reverse("election-conclude", args=[election.id]),
                data={"skip_tally": "on", "confirm": election.name},
            )
        self.assertEqual(resp.status_code, 302)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)
        self.assertFalse(election.tally_result)

    def test_conclude_post_denied_without_permission(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Conclude election - denied",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(reverse("election-conclude", args=[election.id]), data={})
        self.assertEqual(resp.status_code, 403)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionDetailExtendElectionTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def test_extend_button_visible_only_to_managers_and_above_conclude(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Extend election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Extend Election")
        self.assertNotContains(resp, "Conclude Election")

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-election-extend-action-root")
        self.assertContains(resp, "data-election-conclude-action-root")
        self.assertContains(resp, reverse("api-election-extend-end", args=[election.id]))
        self.assertContains(resp, reverse("api-election-conclude", args=[election.id]))
        self.assertNotContains(resp, "Election ID")
        self.assertNotContains(resp, "_modal_name_confirm.html")
        self.assertNotContains(resp, "bindNameConfirm")

        body = resp.content.decode("utf-8")
        self.assertLess(body.find("data-election-extend-action-root"), body.find("data-election-conclude-action-root"))


class ElectionDetailTallyElectionTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _grant_committee_manage_permission(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            permission=ASTRA_ADD_ELECTION,
        )

    def test_tally_button_visible_only_to_managers_for_closed_untallied_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Closed untallied election",
            description="",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-election-tally-action-root")
        self.assertNotContains(response, "Tally Election")

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-election-tally-action-root")
        self.assertContains(response, reverse("api-election-tally", args=[election.id]))
        self.assertContains(response, "Tally Election")
        self.assertNotContains(response, "data-election-conclude-action-root")

    def test_tally_button_hidden_for_tallied_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Tallied election",
            description="",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"elected": []},
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-election-tally-action-root")
        self.assertNotContains(response, "Tally Election")

    def test_tally_button_visible_for_election_committee_group_member(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Closed election for committee",
            description="",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        self._login_as_freeipa_user("committee")
        self._grant_committee_manage_permission()
        committee_user = FreeIPAUser(
            "committee",
            {
                "uid": ["committee"],
                "memberof_group": [settings.FREEIPA_ELECTION_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=committee_user):
            response = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-election-tally-action-root")
        self.assertContains(response, reverse("api-election-tally", args=[election.id]))
        self.assertContains(response, "Tally Election")

    def test_extend_post_requires_new_end_after_current_and_logs_quota_status(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Extend election - post",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        same_end = election.end_datetime
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.post(
                reverse("election-extend-end", args=[election.id]),
                {"end_datetime": timezone.localtime(same_end).strftime("%Y-%m-%dT%H:%M"), "confirm": election.name},
            )
        self.assertEqual(resp.status_code, 302)
        election.refresh_from_db()
        self.assertEqual(
            timezone.localtime(election.end_datetime).strftime("%Y-%m-%dT%H:%M"),
            timezone.localtime(same_end).strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertFalse(
            AuditLogEntry.objects.filter(election=election, event_type="election_end_extended").exists()
        )

        new_end = now + datetime.timedelta(days=2)
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.post(
                reverse("election-extend-end", args=[election.id]),
                {"end_datetime": timezone.localtime(new_end).strftime("%Y-%m-%dT%H:%M"), "confirm": election.name},
            )
        self.assertEqual(resp.status_code, 302)

        election.refresh_from_db()
        self.assertEqual(
            timezone.localtime(election.end_datetime).strftime("%Y-%m-%dT%H:%M"),
            timezone.localtime(new_end).strftime("%Y-%m-%dT%H:%M"),
        )

        entries = list(AuditLogEntry.objects.filter(election=election, event_type="election_end_extended"))
        self.assertEqual(len(entries), 1)
        payload = entries[0].payload if isinstance(entries[0].payload, dict) else {}
        self.assertIn("previous_end_datetime", payload)
        self.assertIn("new_end_datetime", payload)
        self.assertIn("quorum_percent", payload)
        self.assertIn("participating_voter_count", payload)
