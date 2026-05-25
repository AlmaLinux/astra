"""Tests for Plan 130: Tie-break UUID hardening (A) and epsilon/convergence parameter exposure (B).

Covers:
- Candidate.tiebreak_uuid is immutable once the election is started (open/closed/tallied).
- Candidate.tiebreak_uuid remains mutable while the election is in draft.
- Saving a candidate with other field changes (no tiebreak mutation) is allowed on started elections.
- election_started AuditLogEntry payload includes candidate list with tiebreak UUIDs.
- Audit log template renders candidates from election_started payload.
- tally_result["algorithm"] includes epsilon and max_iterations after tally.
"""
import datetime
import uuid
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.election_chain import CHAIN_VERSION_CONFIG_ANCHOR_V2
from core.freeipa.user import FreeIPAUser
from core.models import AuditLogEntry, Ballot, Candidate, Election, ExclusionGroup, ExclusionGroupCandidate
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


def _make_election(status: str, *, chain_version: int = 1) -> Election:
    now = timezone.now()
    return Election.objects.create(
        name=f"Tiebreak hardening test ({status})",
        description="",
        start_datetime=now - datetime.timedelta(days=2),
        end_datetime=now - datetime.timedelta(days=1),
        number_of_seats=1,
        status=status,
        chain_version=chain_version,
    )


def _make_candidate(election: Election, username: str = "alice") -> Candidate:
    return Candidate.objects.create(
        election=election,
        freeipa_username=username,
        nominated_by="nominator",
    )


class CandidateTiebreakImmutabilityTests(TestCase):
    """Candidate.save() must reject tiebreak_uuid changes on non-draft elections."""

    def test_election_chain_version_defaults_to_newest_on_create(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Default chain version election",
            description="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        self.assertEqual(election.chain_version, CHAIN_VERSION_CONFIG_ANCHOR_V2)

    def test_chain_version_immutable_on_draft_election_after_creation(self) -> None:
        election = _make_election(Election.Status.draft, chain_version=1)
        election.chain_version = CHAIN_VERSION_CONFIG_ANCHOR_V2

        with self.assertRaises(ValidationError):
            election.save()

    def test_tiebreak_uuid_immutable_for_open_election(self) -> None:
        election = _make_election(Election.Status.open)
        candidate = _make_candidate(election)
        candidate.tiebreak_uuid = uuid.uuid4()
        with self.assertRaises(ValidationError):
            candidate.save()

    def test_tiebreak_uuid_immutable_for_closed_election(self) -> None:
        election = _make_election(Election.Status.closed)
        candidate = _make_candidate(election)
        candidate.tiebreak_uuid = uuid.uuid4()
        with self.assertRaises(ValidationError):
            candidate.save()

    def test_tiebreak_uuid_immutable_for_tallied_election(self) -> None:
        election = _make_election(Election.Status.tallied)
        candidate = _make_candidate(election)
        candidate.tiebreak_uuid = uuid.uuid4()
        with self.assertRaises(ValidationError):
            candidate.save()

    def test_tiebreak_uuid_mutable_on_draft_election(self) -> None:
        # While the election is in draft, tiebreak_uuid may be changed (e.g. in tests or admin).
        election = _make_election(Election.Status.draft)
        candidate = _make_candidate(election)
        new_uuid = uuid.uuid4()
        candidate.tiebreak_uuid = new_uuid
        candidate.save()  # must not raise
        candidate.refresh_from_db()
        self.assertEqual(candidate.tiebreak_uuid, new_uuid)

    def test_save_other_fields_allowed_on_open_election(self) -> None:
        # Updating non-tiebreak fields (e.g. description) must still work after election starts.
        election = _make_election(Election.Status.open)
        candidate = _make_candidate(election)
        candidate.description = "Updated bio"
        candidate.save()  # must not raise
        candidate.refresh_from_db()
        self.assertEqual(candidate.description, "Updated bio")

    def test_save_other_fields_allowed_on_tallied_election(self) -> None:
        election = _make_election(Election.Status.tallied)
        candidate = _make_candidate(election)
        candidate.description = "Post-tally edit"
        candidate.save()  # must not raise
        candidate.refresh_from_db()
        self.assertEqual(candidate.description, "Post-tally edit")

    def test_v2_candidate_manifest_backed_fields_are_immutable_for_open_election(self) -> None:
        election = _make_election(Election.Status.draft, chain_version=2)
        candidate = _make_candidate(election)
        election.status = Election.Status.open
        election.save(update_fields=["status", "updated_at"])
        candidate.freeipa_username = "mallory"
        with self.assertRaises(ValidationError):
            candidate.save()

    def test_v2_candidate_description_and_url_remain_editable_after_start(self) -> None:
        election = _make_election(Election.Status.draft, chain_version=2)
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            description="before",
            url="https://example.com/candidates/before",
        )
        election.status = Election.Status.open
        election.save(update_fields=["status", "updated_at"])

        candidate.description = "after"
        candidate.url = "https://example.com/candidates/after"
        candidate.save()

        candidate.refresh_from_db()
        self.assertEqual(candidate.description, "after")
        self.assertEqual(candidate.url, "https://example.com/candidates/after")

    def test_v2_election_manifest_backed_fields_are_immutable_after_start(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Started v2",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            chain_version=2,
        )

        election.name = "Tampered"

        with self.assertRaises(ValidationError):
            election.save()

    def test_v2_election_description_and_url_remain_editable_after_start(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Started v2 mutable metadata",
            description="before",
            url="https://example.com/before",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            chain_version=2,
        )

        election.description = "after"
        election.url = "https://example.com/after"
        election.save()

        election.refresh_from_db()
        self.assertEqual(election.description, "after")
        self.assertEqual(election.url, "https://example.com/after")

    def test_v2_exclusion_group_fields_are_immutable_after_start(self) -> None:
        election = _make_election(Election.Status.draft, chain_version=2)
        candidate = _make_candidate(election)
        group = ExclusionGroup.objects.create(election=election, name="Employees", max_elected=1)
        ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=candidate)
        election.status = Election.Status.open
        election.save(update_fields=["status", "updated_at"])

        group.max_elected = 2

        with self.assertRaises(ValidationError):
            group.save()

    def test_v2_exclusion_group_membership_is_immutable_after_start(self) -> None:
        election = _make_election(Election.Status.draft, chain_version=2)
        candidate = _make_candidate(election)
        other_candidate = _make_candidate(election, username="bob")
        group = ExclusionGroup.objects.create(election=election, name="Employees", max_elected=1)
        membership = ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=candidate)
        election.status = Election.Status.open
        election.save(update_fields=["status", "updated_at"])

        with self.assertRaises(ValidationError):
            ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=other_candidate)

        with self.assertRaises(ValidationError):
            membership.delete()


class ElectionChainVersionDatabaseTriggerTests(TransactionTestCase):
    """Database-level trigger must reject ORM-bypassing chain_version writes."""

    def test_chain_version_direct_sql_update_is_rejected_after_insert(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("Election.chain_version trigger coverage requires PostgreSQL.")

        election = _make_election(Election.Status.draft, chain_version=1)

        with self.assertRaises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE core_election SET chain_version = %s WHERE id = %s",
                    [CHAIN_VERSION_CONFIG_ANCHOR_V2, election.id],
                )

        election.refresh_from_db()
        self.assertEqual(election.chain_version, 1)


class ElectionStartedAuditPayloadTests(TestCase):
    """election_started AuditLogEntry payload must include candidate tiebreak UUIDs."""

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_election_started_payload_includes_candidates(self) -> None:
        election = _make_election(Election.Status.open)
        c1 = _make_candidate(election, username="alice")
        c2 = _make_candidate(election, username="charlie")

        genesis_hash = election_genesis_chain_hash(election.id)
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload={
                "eligible_voters": 2,
                "emailed": 2,
                "skipped": 0,
                "failures": 0,
                "genesis_chain_hash": genesis_hash,
                "candidates": [
                    {"id": c1.id, "freeipa_username": c1.freeipa_username, "tiebreak_uuid": str(c1.tiebreak_uuid)},
                    {"id": c2.id, "freeipa_username": c2.freeipa_username, "tiebreak_uuid": str(c2.tiebreak_uuid)},
                ],
            },
            is_public=True,
        )

        entry = AuditLogEntry.objects.get(election=election, event_type="election_started")
        candidates = entry.payload["candidates"]
        self.assertEqual(len(candidates), 2)
        by_username = {c["freeipa_username"]: c for c in candidates}
        self.assertIn("alice", by_username)
        self.assertIn("charlie", by_username)
        self.assertEqual(by_username["alice"]["tiebreak_uuid"], str(c1.tiebreak_uuid))
        self.assertEqual(by_username["charlie"]["tiebreak_uuid"], str(c2.tiebreak_uuid))


class ElectionStartedAuditLogTemplateTests(TestCase):
    """Audit log template must render candidate tiebreak UUIDs from election_started payload."""

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_audit_log_renders_candidates_from_election_started(self) -> None:
        self._login_as_freeipa_user("viewer")

        election = _make_election(Election.Status.closed)
        c1 = _make_candidate(election, username="alice")
        c2 = _make_candidate(election, username="charlie")

        genesis_hash = election_genesis_chain_hash(election.id)
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload={
                "eligible_voters": 2,
                "emailed": 2,
                "skipped": 0,
                "failures": 0,
                "genesis_chain_hash": genesis_hash,
                "candidates": [
                    {"id": c1.id, "freeipa_username": "alice", "tiebreak_uuid": str(c1.tiebreak_uuid)},
                    {"id": c2.id, "freeipa_username": "charlie", "tiebreak_uuid": str(c2.tiebreak_uuid)},
                ],
            },
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-audit-log", args=[election.id]))
            api_resp = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-election-audit-log-root')
        self.assertContains(resp, reverse("api-election-audit-log", args=[election.id]))
        self.assertEqual(api_resp.status_code, 200)
        event_payload = api_resp.json()["audit_log"]["items"][0]["payload"]
        self.assertEqual(
            event_payload,
            {
                "genesis_chain_hash": genesis_hash,
                "candidates": [
                    {"id": c1.id, "freeipa_username": "alice", "tiebreak_uuid": str(c1.tiebreak_uuid)},
                    {"id": c2.id, "freeipa_username": "charlie", "tiebreak_uuid": str(c2.tiebreak_uuid)},
                ],
            },
        )


def _make_tallied_election() -> Election:
    """Create a minimal closed election with one candidate and one ballot, ready to tally."""
    from core import elections_services

    now = timezone.now()
    election = Election.objects.create(
        name="Epsilon param test election",
        description="",
        start_datetime=now - datetime.timedelta(days=10),
        end_datetime=now - datetime.timedelta(days=1),
        number_of_seats=1,
        status=Election.Status.closed,
        chain_version=1,
    )
    c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
    c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

    genesis_hash = election_genesis_chain_hash(election.id)
    ballot_hash = Ballot.compute_hash(
        election_id=election.id,
        credential_public_id="cred-eps",
        ranking=[c1.id, c2.id],
        weight=1,
        nonce="0" * 32,
    )
    chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
    Ballot.objects.create(
        election=election,
        credential_public_id="cred-eps",
        ranking=[c1.id, c2.id],
        weight=1,
        ballot_hash=ballot_hash,
        previous_chain_hash=genesis_hash,
        chain_hash=chain_hash,
    )

    elections_services.tally_election(election=election)
    election.refresh_from_db()
    return election


class TallyAlgorithmParamsTests(TestCase):
    """tally_result must expose epsilon and max_iterations for reproducibility."""

    def test_tally_result_algorithm_includes_epsilon_and_max_iterations(self) -> None:
        """epsilon and max_iterations in tally_result let a third party reproduce convergence."""
        election = _make_tallied_election()
        algo = election.tally_result.get("algorithm", {})
        self.assertIn("epsilon", algo)
        self.assertIn("max_iterations", algo)
        self.assertIsNotNone(algo["epsilon"])
        self.assertEqual(algo["max_iterations"], 200)

