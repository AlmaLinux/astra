from __future__ import annotations

import datetime
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Published election")
        self.assertNotContains(resp, "Draft election")
        self.assertContains(resp, reverse("election-detail", args=[open_election.id]))

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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Draft election")
        self.assertContains(resp, reverse("election-edit", args=[draft_election.id]))
        self.assertContains(resp, reverse("election-detail", args=[open_election.id]))


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

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Open elections")
        self.assertContains(resp, "Past elections")
        self.assertContains(resp, "collapsed-card")
        self.assertContains(resp, "Open election")
        self.assertContains(resp, "Past election")
        self.assertContains(resp, reverse("election-detail", args=[past_election.id]))


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
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Visible election")
        self.assertNotContains(resp, "Deleted election")
        self.assertNotContains(resp, reverse("election-detail", args=[deleted.id]))

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
        with patch("core.backends.FreeIPAUser.get", return_value=admin):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Visible election")
        self.assertNotContains(resp, "Deleted election")
        self.assertNotContains(resp, reverse("election-edit", args=[deleted.id]))

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
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[deleted.id]))
        self.assertEqual(resp.status_code, 404)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionDetailManagerUIStatsTests(TestCase):
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
            isIndividual=True,
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
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
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Participation so far")
        self.assertContains(resp, "Number of unique voters")
        self.assertContains(resp, "Votes cast")
        self.assertContains(resp, "Quorum")

        # ChartJS turnout timeline.
        self.assertContains(resp, 'id="election-turnout-chart"')
        self.assertContains(resp, 'id="election-turnout-chart-data"')
        self.assertContains(resp, 'src="/static/core/vendor/chartjs/chart.umd.min.js"')
        self.assertContains(resp, "election_turnout_chart.js", html=False)
        self.assertNotContains(resp, "cdn.jsdelivr.net/npm/chart.js")

    def test_election_voting_window_renders_in_users_timezone(self) -> None:
        # If the user has a FreeIPA timezone configured, our middleware activates it.
        # The UI should therefore display election datetimes in that timezone.
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

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        # 12:00Z -> 13:00 Europe/Paris (winter), 14:00Z -> 15:00.
        self.assertContains(resp, "2026-01-02 13:00")
        self.assertContains(resp, "2026-01-02 15:00")

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
            patch("core.views_elections.timezone.localdate", side_effect=_localdate_side_effect),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)

        # Extract json_script payload.
        marker = 'id="election-turnout-chart-data"'
        html = resp.content.decode("utf-8")
        idx = html.find(marker)
        self.assertNotEqual(idx, -1)

        # Very small/targeted parse: find the next '>' and the closing '</script>'.
        start = html.find(">", idx)
        end = html.find("</script>", start)
        payload = json.loads(html[start + 1 : end])

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
            patch("core.views_elections.timezone.localdate", side_effect=_localdate_side_effect),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Participation so far")

        marker = 'id="election-turnout-chart-data"'
        html = resp.content.decode("utf-8")
        idx = html.find(marker)
        self.assertNotEqual(idx, -1)
        start = html.find(">", idx)
        end = html.find("</script>", start)
        payload = json.loads(html[start + 1 : end])

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Participation so far")
        self.assertContains(resp, 'id="election-turnout-chart"')
        self.assertContains(resp, 'id="election-turnout-chart-data"')
        self.assertContains(resp, 'src="/static/core/vendor/chartjs/chart.umd.min.js"')
        self.assertContains(resp, "election_turnout_chart.js", html=False)

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Eligible voters")

        # New UI: managers can see ineligible voters and click to view details.
        self.assertContains(resp, "Ineligible voters")
        self.assertContains(resp, 'id="ineligible-voters-card"')
        self.assertContains(resp, 'name="ineligible_q"')
        self.assertContains(resp, 'id="ineligible-voter-details"')
        self.assertContains(resp, "bob")

        # Modal should include the membership term start and election start date.
        self.assertContains(resp, "Membership start")
        self.assertContains(resp, "Election start")
        self.assertContains(resp, "2026-02-01")
        self.assertContains(resp, "2026-02-10")

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        self.assertIn('"days_at_start": 0', html)
        self.assertIn("d.days_at_start == null ? '' : d.days_at_start", html)
        self.assertIn("d.days_short == null ? '' : d.days_short", html)

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ineligible voters")
        self.assertContains(resp, 'id="ineligible-voters-card"')
        self.assertContains(resp, "No ineligible voters found.")

        html = resp.content.decode("utf-8")
        idx = html.find('id="ineligible-voters-card"')
        self.assertNotEqual(idx, -1)
        card_snippet = html[idx : idx + 800]
        self.assertIn("collapsed-card", card_snippet)
        self.assertIn('style="display: none;"', card_snippet)

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
        ):
            resp = self.client.get(
                reverse("election-detail", args=[election.id]),
                {"ineligible_q": "bo"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="ineligible_q"')
        self.assertContains(resp, "bob")
        self.assertNotContains(resp, "alice")

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
        ):
            resp = self.client.get(
                reverse("election-detail", args=[election.id]),
                {"eligible_q": "ali"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="eligible_q"')
        self.assertContains(resp, 'value="ali"')
        self.assertContains(resp, "alice")
        self.assertNotContains(resp, "bob")

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
            isIndividual=True,
            enabled=True,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch(
                "core.views_elections.elections_eligibility._freeipa_group_recursive_member_usernames",
                return_value={"nomember"},
            ),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
            patch("core.views_elections.timezone.now", return_value=now),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ineligible voters")
        self.assertContains(resp, 'id="ineligible-voter-details"')
        self.assertContains(resp, '"nomember"')
        self.assertContains(resp, '"no_membership"')

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
            isIndividual=True,
            enabled=True,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        freeipa_users = [FreeIPAUser("nomember", {"uid": ["nomember"], "memberof_group": []})]

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
            patch("core.views_elections.timezone.now", return_value=now),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ineligible voters")
        self.assertContains(resp, 'id="ineligible-voter-details"')
        self.assertContains(resp, '"nomember"')
        self.assertContains(resp, '"no_membership"')

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
            isIndividual=True,
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
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=freeipa_users),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ineligible voters")
        self.assertContains(resp, 'id="ineligible-voter-details"')
        self.assertContains(resp, '"expireduser"')
        self.assertContains(resp, '"expired"')

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
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Employees of X")
        self.assertContains(resp, "exclusion group")
        self.assertContains(resp, "only 1")


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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
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
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Conclude Election")
        self.assertContains(resp, "Close election, but do not tally votes")
        self.assertContains(resp, "Type the election name to confirm")
        self.assertNotContains(resp, "Election ID")
        self.assertContains(resp, 'id="conclude-submit"')
        self.assertContains(resp, 'id="conclude-submit" disabled')

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

        with patch("core.backends.FreeIPAUser.get", return_value=admin):
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

        with patch("core.backends.FreeIPAUser.get", return_value=admin):
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

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
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
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Extend Election")
        self.assertNotContains(resp, "Conclude Election")

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Extend Election")
        self.assertContains(resp, "Conclude Election")
        self.assertContains(resp, "Type the election name to confirm")
        self.assertNotContains(resp, "Election ID")
        self.assertContains(resp, 'id="extend-submit"')
        self.assertContains(resp, 'id="extend-submit" disabled')

        body = resp.content.decode("utf-8")
        self.assertLess(body.find("Extend Election"), body.find("Conclude Election"))

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
        with patch("core.backends.FreeIPAUser.get", return_value=admin):
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
        with patch("core.backends.FreeIPAUser.get", return_value=admin):
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
