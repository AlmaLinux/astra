
import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import elections_services
from core.freeipa.user import FreeIPAUser
from core.models import AuditLogEntry, Ballot, Candidate, Election, FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class ElectionAuditLogPageTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_election_detail_shows_audit_log_button_when_tallied(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Audit election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"quota": "1", "elected": [], "eliminated": [], "forced_excluded": [], "rounds": []},
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        nominator = FreeIPAUser("nominator", {"uid": ["nominator"], "memberof_group": []})

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)

        audit_url = reverse("election-audit-log", args=[election.id])
        ballots_url = reverse("election-public-ballots", args=[election.id])
        audit_json_url = reverse("election-public-audit", args=[election.id])

        self.assertContains(resp, f'href="{audit_url}"')

        # Keep the Audit Log URL above the existing download URLs in the action-card shell.
        html = resp.content.decode("utf-8")
        self.assertLess(html.find(audit_url), html.find(ballots_url))
        self.assertLess(html.find(audit_url), html.find(audit_json_url))

    def test_audit_log_page_is_vue_shell_without_context_builder(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Audit log shell",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch("core.views_elections.audit._build_election_audit_log_context") as build_context,
        ):
            resp = self.client.get(reverse("election-audit-log", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        build_context.assert_not_called()
        self.assertContains(resp, 'data-election-audit-log-root')
        self.assertContains(resp, reverse("api-election-audit-log", args=[election.id]))
        self.assertContains(resp, reverse("api-election-audit-summary", args=[election.id]))

    def test_audit_log_api_returns_timeline_with_tally_rounds(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Audit log timeline",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        elections_services.tally_election(election=election)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")
            summary_resp = self.client.get(reverse("api-election-audit-summary", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "Iteration 1")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        event_text = "\n".join(str(item.get("summary_text") or "") for item in payload["audit_log"]["items"])
        # The Meek tally always emits iteration summaries.
        self.assertIn("Iteration 1", event_text)
        # Candidate IDs should not appear in summaries/audit text.
        self.assertNotIn(f"(#{c1.id})", event_text)
        # Quota is floor(total/(seats+1)) + 1 = floor(1/2) + 1 = 1.
        self.assertEqual(summary_resp.status_code, 200)
        self.assertEqual(str(summary_resp.json()["summary"]["quota"]), "1")

    def test_audit_log_shows_full_name_and_username_for_elected_candidates_in_both_sections(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Audit log elected display",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={
                "quota": "1",
                "elected": [],
                "eliminated": [],
                "forced_excluded": [],
                "rounds": [],
            },
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        election.tally_result = {
            "quota": "1",
            "elected": [candidate.id],
            "eliminated": [],
            "forced_excluded": [],
            "rounds": [],
        }
        election.save(update_fields=["tally_result"])

        AuditLogEntry.objects.create(
            election=election,
            event_type="tally_completed",
            payload={"elected": [candidate.id]},
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "cn": ["Alice Candidate"], "memberof_group": []})

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")
            summary_resp = self.client.get(reverse("api-election-audit-summary", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "Alice Candidate")

        self.assertEqual(api_resp.status_code, 200)
        completed = next(item for item in api_resp.json()["audit_log"]["items"] if item["event_type"] == "tally_completed")
        self.assertEqual(completed["elected_users"], [{"username": "alice", "full_name": "Alice Candidate"}])

        self.assertEqual(summary_resp.status_code, 200)
        self.assertEqual(summary_resp.json()["summary"]["tally_elected_users"], [{"username": "alice", "full_name": "Alice Candidate"}])

    def test_tally_round_keeps_previous_round_elected_candidate_marked_elected(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Audit log cumulative elected",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"quota": "1", "elected": [], "eliminated": [], "forced_excluded": [], "rounds": []},
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        round_one = AuditLogEntry.objects.create(
            election=election,
            event_type="tally_round",
            payload={
                "round": 1,
                "iteration": 1,
                "retained_totals": {str(candidate.id): "1.0000"},
                "retention_factors": {str(candidate.id): "1.0000"},
                "elected": [candidate.id],
                "eliminated": None,
            },
            is_public=True,
        )
        round_two = AuditLogEntry.objects.create(
            election=election,
            event_type="tally_round",
            payload={
                "round": 2,
                "iteration": 2,
                "retained_totals": {str(candidate.id): "1.0000"},
                "retention_factors": {str(candidate.id): "1.0000"},
                "elected": [],
                "eliminated": None,
            },
            is_public=True,
        )
        AuditLogEntry.objects.filter(id=round_one.id).update(timestamp=now - datetime.timedelta(minutes=2))
        AuditLogEntry.objects.filter(id=round_two.id).update(timestamp=now - datetime.timedelta(minutes=1))

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(api_resp.status_code, 200)

        events = api_resp.json()["audit_log"]["items"]
        round_two_event = next(
            event
            for event in events
            if event.get("event_type") == "tally_round" and event.get("title") == "Tally round 2"
        )
        candidate_row = next(row for row in round_two_event["round_rows"] if row["candidate_id"] == candidate.id)

        self.assertTrue(candidate_row["is_elected"])
        self.assertFalse(candidate_row["is_eliminated"])

    def test_audit_log_page_renders_tally_sankey_chart(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Audit log sankey",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        elections_services.tally_election(election=election)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")
            summary_resp = self.client.get(reverse("api-election-audit-summary", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertContains(page_resp, "chartjs-chart-sankey.js", html=False)
        self.assertNotContains(page_resp, "tally-sankey-data")
        self.assertNotContains(page_resp, "Tally round 1")

        self.assertEqual(api_resp.status_code, 200)
        self.assertTrue(any(item["title"] == "Tally round 1" for item in api_resp.json()["audit_log"]["items"]))

        self.assertEqual(summary_resp.status_code, 200)
        self.assertGreater(len(summary_resp.json()["summary"]["sankey_flows"]), 0)

    def test_audit_log_groups_ballot_submissions_by_day_for_managers(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Long election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        day1 = (now - datetime.timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        day2 = (now - datetime.timedelta(days=2)).replace(hour=11, minute=0, second=0, microsecond=0)

        e1 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-1"},
            is_public=False,
        )
        e2 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-2"},
            is_public=False,
        )
        e3 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-3"},
            is_public=False,
        )
        e4 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-4"},
            is_public=False,
        )

        AuditLogEntry.objects.filter(id=e1.id).update(timestamp=day1)
        AuditLogEntry.objects.filter(id=e2.id).update(timestamp=day1 + datetime.timedelta(hours=1))
        AuditLogEntry.objects.filter(id=e3.id).update(timestamp=day1 + datetime.timedelta(hours=2))
        AuditLogEntry.objects.filter(id=e4.id).update(timestamp=day2)

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "hash-1")

        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertTrue(any(item["title"] == "Ballots submitted" for item in payload["audit_log"]["items"]))
        ballot_hashes = {
            entry["ballot_hash"]
            for item in payload["audit_log"]["items"]
            for entry in item.get("ballot_entries", [])
        }
        self.assertEqual(ballot_hashes, {"hash-1", "hash-2", "hash-3", "hash-4"})

    def test_audit_log_renders_election_closed_and_anonymized_events_prettily(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Closed election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        # Create election_closed event
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={"chain_head": "a" * 64},
            is_public=True,
        )

        # Create election_anonymized event
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_anonymized",
            payload={"credentials_affected": 5, "emails_scrubbed": 10},
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "aaaaaaaaaaaaaaaa")

        self.assertEqual(api_resp.status_code, 200)
        events = api_resp.json()["audit_log"]["items"]
        closed = next(item for item in events if item["event_type"] == "election_closed")
        self.assertEqual(closed["title"], "Election closed")
        self.assertEqual(closed["payload"]["chain_head"], "a" * 64)

        anonymized = next(item for item in events if item["event_type"] == "election_anonymized")
        self.assertEqual(anonymized["title"], "Election anonymized")
        self.assertEqual(anonymized["payload"]["credentials_affected"], 5)
        self.assertEqual(anonymized["payload"]["emails_scrubbed"], 10)

    def test_audit_log_hides_quorum_reached_for_non_managers(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Quorum privacy election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="quorum_reached",
            payload={"quorum_percent": 50},
            is_public=True,
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={
                "chain_head": "b" * 64,
                "credentials_affected": 9,
                "emails_scrubbed": 8,
            },
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "Quorum reached")

        self.assertEqual(api_resp.status_code, 200)
        events = api_resp.json()["audit_log"]["items"]
        self.assertNotIn("quorum_reached", {item["event_type"] for item in events})
        closed = next(item for item in events if item["event_type"] == "election_closed")
        self.assertEqual(closed["payload"]["chain_head"], "b" * 64)
        self.assertTrue(closed["payload"]["credentials_affected"])
        self.assertTrue(closed["payload"]["emails_scrubbed"])

    def test_audit_log_shows_election_anonymized_counts_for_managers(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Anonymized manager visibility election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="election_anonymized",
            payload={"credentials_affected": 5, "emails_scrubbed": 10},
            is_public=False,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "Election anonymized")

        self.assertEqual(api_resp.status_code, 200)
        anonymized = next(item for item in api_resp.json()["audit_log"]["items"] if item["event_type"] == "election_anonymized")
        self.assertEqual(anonymized["payload"]["credentials_affected"], 5)
        self.assertEqual(anonymized["payload"]["emails_scrubbed"], 10)

    def test_audit_log_shows_quorum_reached_for_election_managers(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Quorum manager visibility election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="quorum_reached",
            payload={"quorum_percent": 50},
            is_public=True,
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={
                "chain_head": "e" * 64,
                "credentials_affected": 12,
                "emails_scrubbed": 10,
            },
            is_public=True,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            page_resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(reverse("api-election-audit-log", args=[election.id]), HTTP_ACCEPT="application/json")

        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'data-election-audit-log-root')
        self.assertNotContains(page_resp, "Quorum reached")

        self.assertEqual(api_resp.status_code, 200)
        events = api_resp.json()["audit_log"]["items"]
        self.assertIn("quorum_reached", {item["event_type"] for item in events})
        closed = next(item for item in events if item["event_type"] == "election_closed")
        self.assertEqual(closed["payload"], {"chain_head": "e" * 64})
