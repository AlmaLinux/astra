import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.management.commands.elections_reset import _dt
from core.models import AuditLogEntry, Ballot, Candidate, Election, FreeIPAPermissionGrant, VotingCredential
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.utils_test_data import ensure_core_categories
from core.tokens import election_chain_next_hash, election_genesis_chain_hash


class ElectionsResetCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()

    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _run_reset(self) -> dict[str, object]:
        call_command("auth_profile_reset")
        stdout = io.StringIO()
        call_command("elections_reset", stdout=stdout)
        return json.loads(stdout.getvalue())

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("elections_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_deterministic_wave6_payload_and_real_list_detail_verify_routes(self) -> None:
        first_payload = self._run_reset()
        second_payload = self._run_reset()

        self.assertEqual(first_payload["scenario"], "elections")
        self.assertEqual(second_payload["scenario"], "elections")
        self.assertEqual(first_payload["status"], "reset")
        self.assertEqual(second_payload["status"], "reset")
        self.assertEqual(first_payload["actors"], second_payload["actors"])
        self.assertEqual(set(first_payload["actors"].keys()), {"viewer", "manager"})
        self.assertEqual(
            set(first_payload["elections"].keys()),
            {
                "open_list_election",
                "past_list_election",
                "draft_manager_election",
                "manager_open_election",
                "detail_open_election",
                "detail_tallied_election",
            },
        )
        self.assertEqual(
            set(first_payload["receipts"].keys()),
            {"verify_closed_receipt", "verify_tallied_receipt", "verify_superseded_receipt"},
        )
        self.assertEqual(set(second_payload["receipts"].keys()), set(first_payload["receipts"].keys()))
        self.assertEqual(
            set(first_payload["scenarios"].keys()),
            {
                "elections-algorithm-shell",
                "elections-audit-log-finished-shell",
                "elections-email-closed-send",
                "elections-email-open-reminder",
                "elections-email-tallied-send",
                "elections-list-viewer-shell",
                "elections-list-manager-draft-routing",
                "elections-detail-open-summary",
                "elections-detail-operator-actions",
                "elections-detail-tallied-results",
                "elections-ballot-verify-closed-public-state",
                "elections-ballot-verify-tallied-public-states",
                "elections-edit-draft-save-and-start",
                "elections-edit-manage-candidates-exclusion-groups-and-email",
                "elections-turnout-report-shell",
                "elections-vote-ineligible-state",
                "elections-vote-ranking-submit-and-copy-receipt",
            },
        )
        self.assertEqual(set(second_payload["scenarios"].keys()), set(first_payload["scenarios"].keys()))
        self.assertEqual(
            {alias: election["name"] for alias, election in first_payload["elections"].items()},
            {alias: election["name"] for alias, election in second_payload["elections"].items()},
        )
        self.assertEqual(
            first_payload["elections"]["open_list_election"]["id"],
            second_payload["elections"]["open_list_election"]["id"],
        )
        self.assertEqual(
            first_payload["elections"]["draft_manager_election"]["id"],
            second_payload["elections"]["draft_manager_election"]["id"],
        )
        self.assertEqual(
            first_payload["elections"]["manager_open_election"]["id"],
            second_payload["elections"]["manager_open_election"]["id"],
        )
        self.assertEqual(
            first_payload["elections"]["past_list_election"]["id"],
            second_payload["elections"]["past_list_election"]["id"],
        )
        self.assertEqual(
            first_payload["elections"]["detail_tallied_election"]["id"],
            second_payload["elections"]["detail_tallied_election"]["id"],
        )
        self.assertEqual(first_payload["routes"]["ballot_verify"], reverse("ballot-verify"))
        self.assertEqual(
            first_payload["routes"]["open_detail"],
            first_payload["elections"]["detail_open_election"]["route"],
        )
        self.assertEqual(
            first_payload["routes"]["tallied_detail"],
            first_payload["elections"]["detail_tallied_election"]["route"],
        )

        manager_username = first_payload["actors"]["manager"]["username"]
        viewer_username = first_payload["actors"]["viewer"]["username"]
        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_ADD_ELECTION,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=manager_username,
            ).exists()
        )

        tallied_election = Election.objects.get(pk=first_payload["elections"]["detail_tallied_election"]["id"])
        closed_election = Election.objects.get(pk=first_payload["elections"]["past_list_election"]["id"])
        self.assertEqual(closed_election.status, Election.Status.closed)
        self.assertEqual(tallied_election.status, Election.Status.tallied)
        self.assertTrue(
            AuditLogEntry.objects.filter(election=tallied_election, event_type="ballot_submitted").exists()
        )

        self._login_as_freeipa(viewer_username)
        viewer_list_response = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")
        self.assertEqual(viewer_list_response.status_code, 200)
        viewer_names = [item["name"] for item in viewer_list_response.json()["items"]]
        self.assertIn("Wave 6 Open Election", viewer_names)
        self.assertIn("Wave 6 Past Election", viewer_names)
        self.assertNotIn("Wave 6 Draft Election", viewer_names)

        open_detail_id = first_payload["elections"]["detail_open_election"]["id"]
        open_detail_response = self.client.get(
            reverse("api-election-detail-page", args=[open_detail_id]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(open_detail_response.status_code, 200)
        open_detail_payload = open_detail_response.json()["election"]
        self.assertEqual(open_detail_payload["status"], "open")
        self.assertEqual(open_detail_payload["name"], "Wave 6 Open Election")
        self.assertFalse(open_detail_payload["show_turnout_chart"])

        open_candidates_response = self.client.get(
            reverse("api-election-detail-candidates", args=[open_detail_id]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(open_candidates_response.status_code, 200)
        open_candidates = open_candidates_response.json()["candidates"]["items"]
        self.assertEqual([candidate["username"] for candidate in open_candidates], ["alice", "bob"])

        self._login_as_freeipa(manager_username)
        manager_list_response = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")
        self.assertEqual(manager_list_response.status_code, 200)
        manager_names = [item["name"] for item in manager_list_response.json()["items"]]
        self.assertIn("Wave 6 Draft Election", manager_names)

        tallied_detail_id = first_payload["elections"]["detail_tallied_election"]["id"]
        tallied_detail_response = self.client.get(
            reverse("api-election-detail-page", args=[tallied_detail_id]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(tallied_detail_response.status_code, 200)
        tallied_detail_payload = tallied_detail_response.json()["election"]
        self.assertEqual(tallied_detail_payload["status"], "tallied")
        self.assertTrue(tallied_detail_payload["show_turnout_chart"])
        self.assertGreater(len(tallied_detail_payload["turnout_rows"]), 0)
        self.assertEqual(
            [winner["username"] for winner in tallied_detail_payload["tally_winners"]],
            ["alice"],
        )
        self.assertEqual(tallied_detail_payload["empty_seats"], 1)

        closed_verify_response = self.client.get(
            reverse("api-ballot-verify"),
            data={"receipt": first_payload["receipts"]["verify_closed_receipt"]["ballot_hash"]},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(closed_verify_response.status_code, 200)
        closed_verify_payload = closed_verify_response.json()
        self.assertEqual(closed_verify_payload["election_status"], "closed")
        self.assertTrue(closed_verify_payload["is_final_ballot"])
        self.assertFalse(closed_verify_payload["is_superseded"])

        tallied_verify_response = self.client.get(
            reverse("api-ballot-verify"),
            data={"receipt": first_payload["receipts"]["verify_tallied_receipt"]["ballot_hash"]},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(tallied_verify_response.status_code, 200)
        tallied_verify_payload = tallied_verify_response.json()
        self.assertEqual(tallied_verify_payload["election_status"], "tallied")
        self.assertTrue(tallied_verify_payload["is_final_ballot"])
        self.assertFalse(tallied_verify_payload["is_superseded"])

        superseded_verify_response = self.client.get(
            reverse("api-ballot-verify"),
            data={"receipt": first_payload["receipts"]["verify_superseded_receipt"]["ballot_hash"]},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(superseded_verify_response.status_code, 200)
        superseded_verify_payload = superseded_verify_response.json()
        self.assertEqual(superseded_verify_payload["election_status"], "tallied")
        self.assertFalse(superseded_verify_payload["is_final_ballot"])
        self.assertTrue(superseded_verify_payload["is_superseded"])

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_fake_election_committee_group_for_candidate_search(self) -> None:
        payload = self._run_reset()
        manager_username = payload["actors"]["manager"]["username"]
        draft_election_id = payload["elections"]["draft_manager_election"]["id"]

        self._login_as_freeipa(manager_username)

        candidate_response = self.client.get(
            reverse("election-eligible-users-search", args=[draft_election_id]),
            {
                "q": "regular18",
                "eligible_group_cn": "wave6-e2e-electorate",
            },
            HTTP_ACCEPT="application/json",
        )
        nominator_response = self.client.get(
            reverse("election-nomination-users-search", args=[draft_election_id]),
            {
                "q": "regular19",
            },
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(candidate_response.status_code, 200)
        self.assertEqual(nominator_response.status_code, 200)
        self.assertEqual(
            candidate_response.json()["results"],
            [{"id": "regular18", "text": "Regular 18 User (regular18)"}],
        )
        self.assertEqual(
            nominator_response.json()["results"],
            [{"id": "regular19", "text": "Regular 19 User (regular19)"}],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_emits_extended_routes_and_scenarios_for_vote_report_audit_algorithm_and_edit_workflows(self) -> None:
        payload = self._run_reset()

        self.assertEqual(
            set(payload["routes"].keys()),
            {
                "algorithm",
                "audit_tallied",
                "ballot_verify",
                "closed_detail",
                "edit_draft",
                "open_detail",
                "open_vote",
                "tallied_detail",
                "turnout_report",
            },
        )
        self.assertEqual(
            set(payload["scenarios"].keys()),
            {
                "elections-algorithm-shell",
                "elections-audit-log-finished-shell",
                "elections-ballot-verify-closed-public-state",
                "elections-ballot-verify-tallied-public-states",
                "elections-detail-open-summary",
                "elections-detail-operator-actions",
                "elections-detail-tallied-results",
                "elections-edit-draft-save-and-start",
                "elections-edit-manage-candidates-exclusion-groups-and-email",
                "elections-email-closed-send",
                "elections-email-open-reminder",
                "elections-email-tallied-send",
                "elections-list-manager-draft-routing",
                "elections-list-viewer-shell",
                "elections-turnout-report-shell",
                "elections-vote-ineligible-state",
                "elections-vote-ranking-submit-and-copy-receipt",
            },
        )

        self.assertEqual(payload["routes"]["open_vote"], payload["scenarios"]["elections-vote-ranking-submit-and-copy-receipt"]["route_target"])
        self.assertEqual(payload["routes"]["turnout_report"], payload["scenarios"]["elections-turnout-report-shell"]["route_target"])
        self.assertEqual(payload["routes"]["audit_tallied"], payload["scenarios"]["elections-audit-log-finished-shell"]["route_target"])
        self.assertEqual(payload["routes"]["algorithm"], payload["scenarios"]["elections-algorithm-shell"]["route_target"])
        self.assertEqual(payload["routes"]["edit_draft"], payload["scenarios"]["elections-edit-draft-save-and-start"]["route_target"])

        self._login_as_freeipa(payload["actors"]["manager"]["username"])
        vote_response = self.client.get(payload["routes"]["open_vote"], HTTP_ACCEPT="text/html")
        self.assertEqual(vote_response.status_code, 200)
        self.assertContains(vote_response, "data-election-vote-root", html=False)

        turnout_response = self.client.get(payload["routes"]["turnout_report"], HTTP_ACCEPT="text/html")
        self.assertEqual(turnout_response.status_code, 200)
        self.assertContains(turnout_response, "data-elections-turnout-report-root", html=False)

        audit_response = self.client.get(payload["routes"]["audit_tallied"], HTTP_ACCEPT="text/html")
        self.assertEqual(audit_response.status_code, 200)
        self.assertContains(audit_response, "data-election-audit-log-root", html=False)

        edit_response = self.client.get(payload["routes"]["edit_draft"], HTTP_ACCEPT="text/html")
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "Save Draft", html=False)
        self.assertContains(edit_response, "Start Election", html=False)

        self._login_as_freeipa(payload["actors"]["viewer"]["username"])
        detail_response = self.client.get(
            reverse("api-election-detail-page", args=[payload["elections"]["detail_open_election"]["id"]]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertFalse(detail_response.json()["election"]["can_vote"])

        algorithm_response = self.client.get(payload["routes"]["algorithm"], HTTP_ACCEPT="text/html")
        self.assertEqual(algorithm_response.status_code, 200)
        self.assertContains(algorithm_response, "verify-ballot-chain.py", html=False)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_closed_and_tallied_elections_via_real_lifecycle_audit_trail(self) -> None:
        payload = self._run_reset()

        open_election = Election.objects.get(pk=payload["elections"]["detail_open_election"]["id"])
        closed_election = Election.objects.get(pk=payload["elections"]["past_list_election"]["id"])
        tallied_election = Election.objects.get(pk=payload["elections"]["detail_tallied_election"]["id"])

        self.assertTrue(
            AuditLogEntry.objects.filter(election=open_election, event_type="election_started", is_public=True).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(election=closed_election, event_type="election_started", is_public=True).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(election=closed_election, event_type="election_closed", is_public=True).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(election=tallied_election, event_type="election_started", is_public=True).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(election=tallied_election, event_type="election_closed", is_public=True).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(election=tallied_election, event_type="tally_completed", is_public=True).exists()
        )
        self.assertGreater(
            AuditLogEntry.objects.filter(election=tallied_election, event_type="tally_round", is_public=True).count(),
            0,
        )

        self.assertFalse(
            VotingCredential.objects.filter(election=closed_election, freeipa_username__isnull=False).exists()
        )
        self.assertFalse(
            VotingCredential.objects.filter(election=tallied_election, freeipa_username__isnull=False).exists()
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_reconverges_slice_owned_candidates_credentials_and_turnout_history(self) -> None:
        first_payload = self._run_reset()
        tallied_election = Election.objects.get(pk=first_payload["elections"]["detail_tallied_election"]["id"])

        Candidate.objects.create(
            election=tallied_election,
            freeipa_username="carol",
            nominated_by="regular29",
            description="Stale local candidate.",
        )
        stale_credential = VotingCredential.objects.filter(election=tallied_election).order_by("public_id").first()
        assert stale_credential is not None
        VotingCredential.objects.filter(pk=stale_credential.pk).update(
            public_id="wave6-stale-credential",
        )
        second_payload = self._run_reset()
        reconverged_tallied_election = Election.objects.get(pk=second_payload["elections"]["detail_tallied_election"]["id"])

        self.assertEqual(
            list(
                Candidate.objects.filter(election=reconverged_tallied_election)
                .order_by("freeipa_username")
                .values_list("freeipa_username", flat=True)
            ),
            ["alice", "bob"],
        )
        self.assertEqual(
            list(
                VotingCredential.objects.filter(election=reconverged_tallied_election)
                .order_by("public_id")
                .values_list("public_id", flat=True)
            ),
            [
                "wave6-tallied-credential-one",
                "wave6-tallied-credential-three",
                "wave6-tallied-credential-two",
            ],
        )
        self.assertEqual(
            list(
                AuditLogEntry.objects.filter(election=reconverged_tallied_election, event_type="ballot_submitted")
                .order_by("timestamp", "id")
                .values_list("timestamp", flat=True)
            ),
            [
                _dt(year=2026, month=2, day=5, hour=12),
                _dt(year=2026, month=2, day=6, hour=12),
                _dt(year=2026, month=2, day=6, hour=13),
            ],
        )

        self._login_as_freeipa(second_payload["actors"]["manager"]["username"])
        tallied_detail_response = self.client.get(
            reverse("api-election-detail-page", args=[reconverged_tallied_election.id]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(tallied_detail_response.status_code, 200)
        self.assertEqual(
            tallied_detail_response.json()["election"]["turnout_rows"],
            [
                {"day": "2026-02-05", "count": 1},
                {"day": "2026-02-06", "count": 2},
            ],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_restores_closed_and_tallied_workflow_audit_rows_when_ballots_already_exist(self) -> None:
        first_payload = self._run_reset()
        closed_election = Election.objects.get(pk=first_payload["elections"]["past_list_election"]["id"])
        tallied_election = Election.objects.get(pk=first_payload["elections"]["detail_tallied_election"]["id"])

        AuditLogEntry.objects.filter(election=closed_election, event_type="election_closed").delete()
        AuditLogEntry.objects.filter(election=tallied_election, event_type="tally_completed").delete()
        AuditLogEntry.objects.filter(election=tallied_election, event_type="tally_round").delete()
        AuditLogEntry.objects.filter(election=tallied_election, event_type="ballot_submitted").delete()

        second_payload = self._run_reset()
        reconverged_closed_election = Election.objects.get(pk=second_payload["elections"]["past_list_election"]["id"])
        reconverged_tallied_election = Election.objects.get(pk=second_payload["elections"]["detail_tallied_election"]["id"])

        self.assertEqual(
            list(
                AuditLogEntry.objects.filter(election=reconverged_closed_election, event_type="election_closed")
                .order_by("timestamp", "id")
                .values_list("timestamp", flat=True)
            ),
            [_dt(year=2026, month=3, day=5, hour=13)],
        )
        self.assertEqual(
            list(
                AuditLogEntry.objects.filter(election=reconverged_tallied_election, event_type="ballot_submitted")
                .order_by("timestamp", "id")
                .values_list("timestamp", flat=True)
            ),
            [
                _dt(year=2026, month=2, day=5, hour=12),
                _dt(year=2026, month=2, day=6, hour=12),
                _dt(year=2026, month=2, day=6, hour=13),
            ],
        )
        self.assertGreater(
            AuditLogEntry.objects.filter(election=reconverged_tallied_election, event_type="tally_round", is_public=True).count(),
            0,
        )
        self.assertEqual(
            list(
                AuditLogEntry.objects.filter(election=reconverged_tallied_election, event_type="tally_completed")
                .order_by("timestamp", "id")
                .values_list("timestamp", flat=True)
            ),
            [_dt(year=2026, month=2, day=7, hour=11)],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_restores_quorum_reached_workflow_rows_when_ballots_already_exist(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            first_payload = self._run_reset()
        closed_election = Election.objects.get(pk=first_payload["elections"]["past_list_election"]["id"])
        tallied_election = Election.objects.get(pk=first_payload["elections"]["detail_tallied_election"]["id"])

        AuditLogEntry.objects.filter(
            election__in=[closed_election, tallied_election],
            event_type="quorum_reached",
        ).delete()

        self.assertFalse(
            AuditLogEntry.objects.filter(
                election__in=[closed_election, tallied_election],
                event_type="quorum_reached",
            ).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            second_payload = self._run_reset()
        reconverged_closed_election = Election.objects.get(pk=second_payload["elections"]["past_list_election"]["id"])
        reconverged_tallied_election = Election.objects.get(pk=second_payload["elections"]["detail_tallied_election"]["id"])

        self.assertEqual(
            list(
                AuditLogEntry.objects.filter(election=reconverged_closed_election, event_type="quorum_reached")
                .order_by("timestamp", "id")
                .values_list("timestamp", flat=True)
            ),
            [_dt(year=2026, month=3, day=5, hour=12)],
        )
        self.assertEqual(
            list(
                AuditLogEntry.objects.filter(election=reconverged_tallied_election, event_type="quorum_reached")
                .order_by("timestamp", "id")
                .values_list("timestamp", flat=True)
            ),
            [_dt(year=2026, month=2, day=5, hour=12)],
        )
        self.assertLess(
            AuditLogEntry.objects.get(election=reconverged_closed_election, event_type="quorum_reached").timestamp,
            AuditLogEntry.objects.get(election=reconverged_closed_election, event_type="election_closed").timestamp,
        )
        self.assertLess(
            AuditLogEntry.objects.get(election=reconverged_tallied_election, event_type="quorum_reached").timestamp,
            AuditLogEntry.objects.get(election=reconverged_tallied_election, event_type="election_closed").timestamp,
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_unexpected_open_election_ballots_before_replaying_open_state(self) -> None:
        first_payload = self._run_reset()
        open_election = Election.objects.get(pk=first_payload["elections"]["detail_open_election"]["id"])
        candidate = Candidate.objects.get(election=open_election, freeipa_username="alice")
        ballot_hash = Ballot.compute_hash(
            election_id=open_election.id,
            credential_public_id="wave6-open-manager-credential",
            ranking=[candidate.id],
            weight=1,
            nonce="6" * 32,
        )

        with patch("django.utils.timezone.now", return_value=timezone.now()):
            Ballot.objects.create(
                election=open_election,
                credential_public_id="wave6-open-manager-credential",
                ranking=[candidate.id],
                weight=1,
                ballot_hash=ballot_hash,
                previous_chain_hash=election_genesis_chain_hash(open_election.id),
                chain_hash=election_chain_next_hash(
                    previous_chain_hash=election_genesis_chain_hash(open_election.id),
                    ballot_hash=ballot_hash,
                ),
            )

        second_payload = self._run_reset()
        reconverged_open_election = Election.objects.get(pk=second_payload["elections"]["detail_open_election"]["id"])

        self.assertEqual(reconverged_open_election.status, Election.Status.open)
        self.assertFalse(Ballot.objects.filter(election=reconverged_open_election).exists())
        self.assertEqual(
            list(
                VotingCredential.objects.filter(election=reconverged_open_election)
                .order_by("public_id")
                .values_list("public_id", flat=True)
            ),
            [
                "wave6-open-candidate-one-credential",
                "wave6-open-candidate-two-credential",
                "wave6-open-manager-credential",
            ],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_retires_unexpected_append_only_ballots_for_tallied_slice_elections(self) -> None:
        first_payload = self._run_reset()
        tallied_election = Election.objects.get(pk=first_payload["elections"]["detail_tallied_election"]["id"])
        candidate = Candidate.objects.get(election=tallied_election, freeipa_username="alice")
        previous_chain_hash = (
            Ballot.objects.for_election(election=tallied_election)
            .latest_chain_head_hash_for_election(election=tallied_election)
        )
        assert previous_chain_hash is not None
        ballot_hash = Ballot.compute_hash(
            election_id=tallied_election.id,
            credential_public_id="wave6-extra-ballot-credential",
            ranking=[candidate.id],
            weight=1,
            nonce="5" * 32,
        )

        with patch("django.utils.timezone.now", return_value=timezone.now()):
            Ballot.objects.create(
                election=tallied_election,
                credential_public_id="wave6-extra-ballot-credential",
                ranking=[candidate.id],
                weight=1,
                ballot_hash=ballot_hash,
                previous_chain_hash=previous_chain_hash,
                chain_hash=election_chain_next_hash(
                    previous_chain_hash=previous_chain_hash,
                    ballot_hash=ballot_hash,
                ),
            )

        second_payload = self._run_reset()
        reconverged_tallied_election = Election.objects.get(pk=second_payload["elections"]["detail_tallied_election"]["id"])

        tallied_election.refresh_from_db()
        self.assertEqual(tallied_election.status, Election.Status.deleted)
        self.assertEqual(reconverged_tallied_election.status, Election.Status.tallied)
        self.assertEqual(Ballot.objects.filter(election=reconverged_tallied_election).count(), 3)
        self.assertEqual(
            list(
                VotingCredential.objects.filter(election=reconverged_tallied_election)
                .order_by("public_id")
                .values_list("public_id", flat=True)
            ),
            [
                "wave6-tallied-credential-one",
                "wave6-tallied-credential-three",
                "wave6-tallied-credential-two",
            ],
        )