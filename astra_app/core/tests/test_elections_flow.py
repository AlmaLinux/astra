
import datetime
import json
import re
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.elections_services import (
    BallotReceipt,
    InvalidCredentialError,
    anonymize_election,
    close_election,
    issue_voting_credential,
    issue_voting_credentials_from_memberships,
    send_vote_receipt_email,
    send_voting_credential_email,
    submit_ballot,
    tally_election,
)
from core.models import AuditLogEntry, Ballot, Candidate, Election, Membership, MembershipType, VotingCredential
from core.tests.ballot_chain import compute_chain_hash
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates
from core.tokens import election_genesis_chain_hash


def setUpModule() -> None:
    ensure_core_categories()
    ensure_email_templates()


class ElectionCredentialAndBallotTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        now = timezone.now()
        self.election = Election.objects.create(
            name="Board election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )
        self.c1 = Candidate.objects.create(
            election=self.election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        self.c2 = Candidate.objects.create(
            election=self.election,
            freeipa_username="bob",
            nominated_by="nominator",
        )

        self.cred = VotingCredential.objects.create(
            election=self.election,
            public_id="cred-public-1",
            freeipa_username="voter1",
            weight=3,
        )

    def test_submit_ballot_last_ballot_wins(self) -> None:
        receipt1 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id, self.c2.id],
        )
        ballot1 = receipt1.ballot
        self.assertEqual(Ballot.objects.filter(election=self.election).count(), 1)
        self.assertEqual(ballot1.ranking, [self.c1.id, self.c2.id])
        self.assertEqual(ballot1.weight, 3)

        receipt2 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c2.id, self.c1.id],
        )
        ballot2 = receipt2.ballot
        self.assertEqual(Ballot.objects.filter(election=self.election).count(), 2)
        self.assertNotEqual(ballot2.id, ballot1.id)
        self.assertNotEqual(ballot2.ballot_hash, ballot1.ballot_hash)
        self.assertEqual(ballot2.ranking, [self.c2.id, self.c1.id])

        events = list(
            AuditLogEntry.objects.filter(election=self.election, event_type="ballot_submitted").values_list(
                "event_type",
                flat=True,
            )
        )
        self.assertEqual(events, ["ballot_submitted", "ballot_submitted"])

    def test_submit_ballot_audit_log_includes_superseded_hash(self) -> None:
        receipt1 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id],
        )
        ballot1 = receipt1.ballot

        receipt2 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c2.id],
        )

        ballot2 = receipt2.ballot

        self.assertNotEqual(ballot2.ballot_hash, ballot1.ballot_hash)

        entries = list(
            AuditLogEntry.objects.filter(election=self.election, event_type="ballot_submitted")
            .order_by("timestamp", "id")
            .values_list("payload", flat=True)
        )
        self.assertEqual(len(entries), 2)

        payload2 = entries[1] if isinstance(entries[1], dict) else {}
        self.assertEqual(payload2.get("supersedes_ballot_hash"), ballot1.ballot_hash)

    def test_submit_ballot_rejects_unknown_credential(self) -> None:
        with self.assertRaises(InvalidCredentialError):
            submit_ballot(
                election=self.election,
                credential_public_id="does-not-exist",
                ranking=[self.c1.id],
            )

    def test_ballot_hash_unique_per_credential(self) -> None:
        cred2 = VotingCredential.objects.create(
            election=self.election,
            public_id="cred-public-2",
            freeipa_username="voter2",
            weight=self.cred.weight,
        )

        receipt1 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id, self.c2.id],
        )
        ballot1 = receipt1.ballot

        receipt2 = submit_ballot(
            election=self.election,
            credential_public_id=cred2.public_id,
            ranking=[self.c1.id, self.c2.id],
        )

        ballot2 = receipt2.ballot

        self.assertNotEqual(ballot2.credential_public_id, ballot1.credential_public_id)
        self.assertNotEqual(ballot2.ballot_hash, ballot1.ballot_hash)

    def test_submit_ballot_same_ranking_produces_distinct_receipts(self) -> None:
        receipt1 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id, self.c2.id],
        )
        ballot1 = receipt1.ballot

        receipt2 = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id, self.c2.id],
        )

        ballot2 = receipt2.ballot

        self.assertNotEqual(ballot2.id, ballot1.id)
        self.assertNotEqual(ballot2.ballot_hash, ballot1.ballot_hash)

    def test_credential_cannot_be_used_across_elections(self) -> None:
        """Regression test: credentials are election-scoped and cannot be reused."""
        from core.elections_services import InvalidCredentialError, submit_ballot

        now = timezone.now()
        election_a = Election.objects.create(
            name="Election A",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        election_b = Election.objects.create(
            name="Election B",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        Candidate.objects.create(election=election_a, freeipa_username="alice", nominated_by="nominator")
        Candidate.objects.create(election=election_b, freeipa_username="bob", nominated_by="nominator")

        cred_a = VotingCredential.objects.create(
            election=election_a,
            public_id="cred-election-a",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election_b,
            public_id="cred-election-b",
            freeipa_username="voter1",
            weight=1,
        )

        with self.assertRaises(InvalidCredentialError):
            submit_ballot(
                election=election_b,
                credential_public_id=cred_a.public_id,
                ranking=[Candidate.objects.filter(election=election_b).first().id],
            )

        self.assertFalse(Ballot.objects.filter(election=election_b).exists())


class ElectionCredentialIssuanceAndAnonymizationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        now = timezone.now()
        self.election = Election.objects.create(
            name="Credential election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

    def test_issue_voting_credential_idempotent_per_user(self) -> None:
        cred1 = issue_voting_credential(
            election=self.election,
            freeipa_username="voter1",
            weight=2,
        )
        cred2 = issue_voting_credential(
            election=self.election,
            freeipa_username="voter1",
            weight=2,
        )

        self.assertEqual(cred1.id, cred2.id)
        self.assertEqual(cred1.public_id, cred2.public_id)
        self.assertEqual(VotingCredential.objects.filter(election=self.election).count(), 1)

    def test_anonymize_election_clears_username(self) -> None:
        cred = issue_voting_credential(
            election=self.election,
            freeipa_username="voter1",
            weight=1,
        )
        self.assertEqual(cred.freeipa_username, "voter1")

        self.election.status = Election.Status.closed
        self.election.save(update_fields=["status"])
        anonymize_election(election=self.election)

        cred.refresh_from_db()
        self.assertIsNone(cred.freeipa_username)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=90)
class ElectionBulkCredentialIssuanceTests(TestCase):
    def test_issue_voting_credentials_from_memberships_applies_eligibility_and_weights(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Bulk issue election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        voter1 = MembershipType.objects.create(
            code="voter1",
            name="Voter type 1",
            votes=1,
            category_id="individual",
        )
        voter2 = MembershipType.objects.create(
            code="voter2",
            name="Voter type 2",
            votes=2,
            category_id="individual",
        )
        nonvoter = MembershipType.objects.create(
            code="nonvoter",
            name="Non voter",
            votes=0,
            category_id="individual",
        )

        eligible_created_at = election.start_datetime - datetime.timedelta(days=100)
        too_recent_created_at = election.start_datetime - datetime.timedelta(days=10)

        m1 = Membership.objects.create(target_username="alice", membership_type=voter1, expires_at=None)
        Membership.objects.filter(pk=m1.pk).update(created_at=eligible_created_at)

        m2 = Membership.objects.create(target_username="alice", membership_type=voter2, expires_at=None)
        Membership.objects.filter(pk=m2.pk).update(created_at=eligible_created_at)

        m3 = Membership.objects.create(target_username="bob", membership_type=voter1, expires_at=None)
        Membership.objects.filter(pk=m3.pk).update(created_at=too_recent_created_at)

        m4 = Membership.objects.create(
            target_username="carol",
            membership_type=voter1,
            expires_at=election.start_datetime - datetime.timedelta(days=1),
        )
        Membership.objects.filter(pk=m4.pk).update(created_at=eligible_created_at)

        m5 = Membership.objects.create(target_username="dave", membership_type=nonvoter, expires_at=None)
        Membership.objects.filter(pk=m5.pk).update(created_at=eligible_created_at)

        affected = len(issue_voting_credentials_from_memberships(election=election))
        self.assertEqual(affected, 1)
        self.assertEqual(VotingCredential.objects.filter(election=election).count(), 1)

        cred = VotingCredential.objects.get(election=election)
        self.assertEqual(cred.freeipa_username, "alice")
        self.assertEqual(cred.weight, 3)

    def test_issue_voting_credentials_from_memberships_updates_weight_without_rotating_public_id(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Bulk update election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        voter = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
        )
        eligible_created_at = election.start_datetime - datetime.timedelta(days=100)
        membership = Membership.objects.create(target_username="alice", membership_type=voter, expires_at=None)
        Membership.objects.filter(pk=membership.pk).update(created_at=eligible_created_at)

        affected1 = len(issue_voting_credentials_from_memberships(election=election))
        self.assertEqual(affected1, 1)
        cred1 = VotingCredential.objects.get(election=election, freeipa_username="alice")

        voter.votes = 5
        voter.save(update_fields=["votes"])

        affected2 = len(issue_voting_credentials_from_memberships(election=election))
        self.assertEqual(affected2, 1)
        cred2 = VotingCredential.objects.get(election=election, freeipa_username="alice")

        self.assertEqual(cred2.id, cred1.id)
        self.assertEqual(cred2.public_id, cred1.public_id)
        self.assertEqual(cred2.weight, 5)


class ElectionPublicExportTests(TestCase):
    def test_public_ballots_export_omits_credential_id(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Export election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-export-1",
            freeipa_username=None,
            weight=1,
        )
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-export-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-export-1",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        url = reverse("election-public-ballots", args=[election.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertIn("ballots", data)
        self.assertIn("chain_head", data)
        self.assertEqual(len(data["ballots"]), 1)
        exported_ballot = data["ballots"][0]
        self.assertIn("ranking", exported_ballot)
        self.assertIn("weight", exported_ballot)
        self.assertIn("ballot_hash", exported_ballot)
        self.assertIn("is_counted", exported_ballot)
        self.assertIn("chain_hash", exported_ballot)
        self.assertIn("previous_chain_hash", exported_ballot)
        self.assertIn("superseded_by", exported_ballot)
        self.assertNotIn("credential_public_id", exported_ballot)

    def test_public_ballots_export_includes_superseded_ballots(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Export superseded election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        c2 = Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by="nominator",
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-export-2",
            freeipa_username=None,
            weight=1,
        )

        receipt1 = submit_ballot(
            election=election,
            credential_public_id="cred-export-2",
            ranking=[c1.id],
        )
        ballot1 = receipt1.ballot

        receipt2 = submit_ballot(
            election=election,
            credential_public_id="cred-export-2",
            ranking=[c2.id],
        )
        ballot2 = receipt2.ballot
        self.assertNotEqual(ballot2.ballot_hash, ballot1.ballot_hash)

        election.status = Election.Status.closed
        election.save(update_fields=["status"])

        url = reverse("election-public-ballots", args=[election.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertIn("ballots", data)
        self.assertIn("chain_head", data)
        self.assertEqual(len(data["ballots"]), 2)

        by_hash = {b["ballot_hash"]: b for b in data["ballots"]}
        self.assertEqual(by_hash[ballot2.ballot_hash]["is_counted"], True)
        self.assertEqual(by_hash[ballot2.ballot_hash]["superseded_by"], None)
        self.assertEqual(by_hash[ballot1.ballot_hash]["is_counted"], False)
        self.assertEqual(by_hash[ballot1.ballot_hash]["superseded_by"], ballot2.ballot_hash)
        self.assertEqual(data["chain_head"], ballot2.chain_hash)

    def test_public_audit_export_only_includes_public_entries(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Audit export election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="internal_note",
            payload={"secret": True},
            is_public=False,
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="tally_round",
            payload={"round": 1},
            is_public=True,
        )

        url = reverse("election-public-audit", args=[election.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual([e["event_type"] for e in data["audit_log"]], ["tally_round"])
        ts = data["audit_log"][0].get("timestamp")
        self.assertIsInstance(ts, str)
        self.assertTrue(re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts))


class ElectionCloseAndTallyTests(TestCase):
    def test_close_election_anonymizes_credentials_and_writes_public_audit(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Close election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="alice",
            weight=1,
        )

        close_election(election=election)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)
        self.assertIsNone(VotingCredential.objects.get(election=election).freeipa_username)

        public_events = list(
            AuditLogEntry.objects.filter(election=election, is_public=True).values_list("event_type", flat=True)
        )
        self.assertIn("election_anonymized", public_events)
        self.assertIn("election_closed", public_events)

    def test_close_election_sets_end_datetime_to_close_time(self) -> None:
        now = timezone.now()
        ended_at = now + datetime.timedelta(hours=2)
        election = Election.objects.create(
            name="Close election end timestamp",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="alice",
            weight=1,
        )

        with patch("core.elections_services.timezone.now", return_value=ended_at):
            close_election(election=election)

        election.refresh_from_db()
        self.assertEqual(election.end_datetime, ended_at)
        self.assertEqual(election.end_datetime.tzinfo, timezone.UTC)

    def test_close_election_includes_final_chain_head_in_public_audit(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Close election chain head",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="alice",
            weight=1,
        )

        ballot_hash_1 = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash_1 = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash_1)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[],
            weight=1,
            ballot_hash=ballot_hash_1,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash_1,
        )

        ballot_hash_2 = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-2",
            ranking=[],
            weight=1,
            nonce="1" * 32,
        )
        chain_hash_2 = compute_chain_hash(previous_chain_hash=chain_hash_1, ballot_hash=ballot_hash_2)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-2",
            ranking=[],
            weight=1,
            ballot_hash=ballot_hash_2,
            previous_chain_hash=chain_hash_1,
            chain_hash=chain_hash_2,
        )

        close_election(election=election)

        entry = AuditLogEntry.objects.filter(election=election, event_type="election_closed", is_public=True).first()
        self.assertIsNotNone(entry)
        self.assertIsInstance(entry.payload, dict)
        self.assertEqual(entry.payload.get("chain_head"), chain_hash_2)

    def test_different_elections_have_unique_genesis_hashes(self) -> None:
        """
        Verify that different elections have different genesis chain hashes.
        This prevents cross-election chain splicing attacks where ballots
        from one election could be incorrectly spliced into another election.
        """
        now = timezone.now()
        election1 = Election.objects.create(
            name="Election 1",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        election2 = Election.objects.create(
            name="Election 2",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        genesis1 = election_genesis_chain_hash(election1.id)
        genesis2 = election_genesis_chain_hash(election2.id)

        # Verify each election has a unique genesis hash
        self.assertNotEqual(genesis1, genesis2, "Different elections must have different genesis hashes")

        # Verify genesis hashes are deterministic
        self.assertEqual(genesis1, election_genesis_chain_hash(election1.id))
        self.assertEqual(genesis2, election_genesis_chain_hash(election2.id))

        # Verify format (64 hex characters)
        self.assertEqual(len(genesis1), 64)
        self.assertEqual(len(genesis2), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in genesis1))
        self.assertTrue(all(c in "0123456789abcdef" for c in genesis2))

    def test_tally_election_persists_result_and_emits_public_round_audit(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Tally election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

        Ballot.objects.create(
            election=election,
            credential_public_id="c1",
            ranking=[c1.id, c2.id],
            weight=1,
            ballot_hash=Ballot.compute_hash(
                election_id=election.id,
                credential_public_id="c1",
                ranking=[c1.id, c2.id],
                weight=1,
                nonce="0" * 32,
            ),
            previous_chain_hash=election_genesis_chain_hash(election.id),
            chain_hash=compute_chain_hash(
                previous_chain_hash=election_genesis_chain_hash(election.id),
                ballot_hash=Ballot.compute_hash(
                    election_id=election.id,
                    credential_public_id="c1",
                    ranking=[c1.id, c2.id],
                    weight=1,
                    nonce="0" * 32,
                ),
            ),
        )

        result = tally_election(election=election)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertIn("elected", election.tally_result)
        self.assertEqual(result["elected"], election.tally_result["elected"])
        self.assertGreaterEqual(len(election.tally_result["rounds"]), 1)

        for round_data in election.tally_result["rounds"]:
            self.assertIn("audit_text", round_data)
            self.assertIn("summary_text", round_data)
            self.assertIsInstance(round_data["audit_text"], str)
            self.assertIsInstance(round_data["summary_text"], str)
            self.assertTrue(round_data["audit_text"].strip())
            self.assertTrue(round_data["summary_text"].strip())

        self.assertTrue(
            AuditLogEntry.objects.filter(election=election, event_type="tally_round", is_public=True).exists()
        )

        for entry in AuditLogEntry.objects.filter(election=election, event_type="tally_round", is_public=True):
            self.assertIn("audit_text", entry.payload)
            self.assertIn("summary_text", entry.payload)
            self.assertIsInstance(entry.payload["audit_text"], str)
            self.assertIsInstance(entry.payload["summary_text"], str)

    def test_tally_applies_exclusion_groups(self) -> None:
        from core.models import ExclusionGroup, ExclusionGroupCandidate

        now = timezone.now()
        election = Election.objects.create(
            name="Tally election with exclusions",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            tiebreak_uuid="00000000-0000-0000-0000-000000000001",
        )
        c2 = Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by="nominator",
            tiebreak_uuid="00000000-0000-0000-0000-000000000002",
        )
        c3 = Candidate.objects.create(
            election=election,
            freeipa_username="carol",
            nominated_by="nominator",
            tiebreak_uuid="00000000-0000-0000-0000-000000000003",
        )

        group = ExclusionGroup.objects.create(election=election, name="Alice-or-Bob", max_elected=1)
        ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=c1)
        ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=c2)

        previous = election_genesis_chain_hash(election.id)
        for i in range(3):
            ballot_hash = Ballot.compute_hash(
                election_id=election.id,
                credential_public_id=f"a-{i}",
                ranking=[c1.id, c2.id, c3.id],
                weight=1,
                nonce="0" * 32,
            )
            chain_hash = compute_chain_hash(previous_chain_hash=previous, ballot_hash=ballot_hash)
            Ballot.objects.create(
                election=election,
                credential_public_id=f"a-{i}",
                ranking=[c1.id, c2.id, c3.id],
                weight=1,
                ballot_hash=ballot_hash,
                previous_chain_hash=previous,
                chain_hash=chain_hash,
            )
            previous = chain_hash

        for i in range(3):
            ballot_hash = Ballot.compute_hash(
                election_id=election.id,
                credential_public_id=f"b-{i}",
                ranking=[c2.id, c1.id, c3.id],
                weight=1,
                nonce="0" * 32,
            )
            chain_hash = compute_chain_hash(previous_chain_hash=previous, ballot_hash=ballot_hash)
            Ballot.objects.create(
                election=election,
                credential_public_id=f"b-{i}",
                ranking=[c2.id, c1.id, c3.id],
                weight=1,
                ballot_hash=ballot_hash,
                previous_chain_hash=previous,
                chain_hash=chain_hash,
            )
            previous = chain_hash

        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="c-0",
            ranking=[c3.id, c1.id, c2.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=previous, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="c-0",
            ranking=[c3.id, c1.id, c2.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=previous,
            chain_hash=chain_hash,
        )

        result = tally_election(election=election)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertIn("forced_excluded", election.tally_result)
        self.assertEqual(result["forced_excluded"], election.tally_result["forced_excluded"])

        # Ensure only one of c1/c2 was elected.
        elected = set(result["elected"])
        self.assertEqual(len(elected & {c1.id, c2.id}), 1)
        self.assertIn(c3.id, elected)

        # Ensure the round audit text references the exclusion group by name.
        rounds = list(election.tally_result.get("rounds") or [])
        rounds_with_forced = [r for r in rounds if isinstance(r, dict) and (r.get("forced_exclusions") or [])]
        self.assertTrue(rounds_with_forced, "expected at least one forced exclusion round")
        audit_text = str(rounds_with_forced[0].get("audit_text") or "")
        self.assertIn("Alice-or-Bob", audit_text)


class ElectionEmailTimezoneTests(TestCase):
    def test_vote_receipt_email_uses_recipient_timezone(self) -> None:
        from post_office.models import Email, EmailTemplate

        EmailTemplate.objects.get_or_create(
            name=settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME,
            defaults={
                "subject": "Receipt",
                "content": "Receipt {{ ballot_hash }} End {{ election_end_datetime }}",
                "html_content": "",
            },
        )

        start_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 0, 0), timezone=timezone.UTC)
        end_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 14, 0, 0), timezone=timezone.UTC)
        election = Election.objects.create(
            name="TZ email election",
            description="",
            start_datetime=start_utc,
            end_datetime=end_utc,
            number_of_seats=1,
            status=Election.Status.open,
        )

        ballot = Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[],
            weight=1,
            ballot_hash="b" * 64,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            chain_hash="1" * 64,
        )
        receipt = BallotReceipt(ballot=ballot, nonce="n" * 32)

        send_vote_receipt_email(
            request=None,
            election=election,
            username="voter1",
            email="voter1@example.com",
            receipt=receipt,
            tz_name="Europe/Paris",
        )

        self.assertEqual(Email.objects.count(), 1)
        queued = Email.objects.first()
        assert queued is not None
        self.assertEqual(queued.to, ["voter1@example.com"])

        ctx = queued.context or {}
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)
        self.assertIn("(Europe/Paris)", str(ctx.get("election_end_datetime") or ""))
        self.assertIn("15:00", str(ctx.get("election_end_datetime") or ""))

    def test_voting_credential_email_uses_recipient_timezone(self) -> None:
        from post_office.models import Email, EmailTemplate

        EmailTemplate.objects.get_or_create(
            name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
            defaults={
                "subject": "Credential",
                "content": "Credential {{ credential_public_id }} Start {{ election_start_datetime }}",
                "html_content": "",
            },
        )

        start_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 0, 0), timezone=timezone.UTC)
        end_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 14, 0, 0), timezone=timezone.UTC)
        election = Election.objects.create(
            name="TZ credential election",
            description="",
            start_datetime=start_utc,
            end_datetime=end_utc,
            number_of_seats=1,
            status=Election.Status.open,
        )

        send_voting_credential_email(
            request=None,
            election=election,
            username="voter1",
            email="voter1@example.com",
            credential_public_id="cred-xyz",
            tz_name="Europe/Paris",
        )

        self.assertEqual(Email.objects.count(), 1)
        queued = Email.objects.first()
        assert queued is not None
        self.assertEqual(queued.to, ["voter1@example.com"])

        ctx = queued.context or {}
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)
        self.assertIn("(Europe/Paris)", str(ctx.get("election_start_datetime") or ""))
        self.assertIn("13:00", str(ctx.get("election_start_datetime") or ""))


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionVoteEndpointTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        self._coc_patcher = patch("core.views_elections.vote.has_signed_coc", return_value=True)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)
        now = timezone.now()
        self.election = Election.objects.create(
            name="Vote election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        self.c1 = Candidate.objects.create(
            election=self.election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        self.c2 = Candidate.objects.create(
            election=self.election,
            freeipa_username="bob",
            nominated_by="nominator",
        )
        self.cred = VotingCredential.objects.create(
            election=self.election,
            public_id="cred-vote-1",
            freeipa_username="voter1",
            weight=2,
        )

        voter = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        Membership.objects.create(
            target_username="voter1",
            membership_type=voter,
            expires_at=None,
        )
        eligible_created_at = self.election.start_datetime - datetime.timedelta(days=2)
        Membership.objects.filter(target_username="voter1").update(created_at=eligible_created_at)

    def test_vote_submit_rejects_credential_belonging_to_different_user(self) -> None:
        self._login_as_freeipa_user("voter2")

        url = reverse("election-vote-submit", args=[self.election.id])

        voter2 = FreeIPAUser("voter2", {"uid": ["voter2"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=voter2):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c1.id],
                },
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_vote_submit_json_creates_or_updates_ballot(self) -> None:
        self._login_as_freeipa_user("voter1")

        url = reverse("election-vote-submit", args=[self.election.id])

        voter1 = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c2.id, self.c1.id],
                },
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("ballot_hash", payload)

        ballot = Ballot.objects.get(election=self.election, credential_public_id=self.cred.public_id)
        self.assertEqual(ballot.ranking, [self.c2.id, self.c1.id])
        self.assertEqual(ballot.weight, 2)

    def test_vote_submit_sends_receipt_email_when_user_has_email(self) -> None:
        from post_office.models import Email, EmailTemplate

        EmailTemplate.objects.get_or_create(
            name=settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME,
            defaults={
                "subject": "Receipt",
                "content": "Receipt {{ ballot_hash }} Nonce {{ nonce }} Verify {{ verify_url }}",
                "html_content": "",
            },
        )

        self._login_as_freeipa_user("voter1")

        url = reverse("election-vote-submit", args=[self.election.id])

        voter1 = FreeIPAUser(
            "voter1",
            {
                "uid": ["voter1"],
                "mail": ["voter1@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c2.id, self.c1.id],
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])

        self.assertEqual(Email.objects.count(), 1)
        queued = Email.objects.first()
        assert queued is not None
        self.assertEqual(queued.to, ["voter1@example.com"])

        ctx = queued.context or {}
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)
        self.assertEqual(ctx.get("ballot_hash"), payload.get("ballot_hash"))
        self.assertEqual(ctx.get("nonce"), payload.get("nonce"))
        self.assertEqual(ctx.get("previous_chain_hash"), payload.get("previous_chain_hash"))
        self.assertEqual(ctx.get("chain_hash"), payload.get("chain_hash"))
        self.assertEqual(ctx.get("weight"), 2)
        self.assertIn(str(payload.get("ballot_hash") or ""), str(ctx.get("verify_url") or ""))
        self.assertNotIn("ranking", ctx)

    @override_settings(ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME="test-vote-receipt")
    def test_vote_submit_queues_post_office_email_row(self) -> None:
        from post_office.models import Email, EmailTemplate

        EmailTemplate.objects.create(
            name="test-vote-receipt",
            subject="Receipt {{ ballot_hash }}",
            content="Receipt {{ ballot_hash }} Nonce {{ nonce }} Verify {{ verify_url }}",
            html_content="",
        )

        self._login_as_freeipa_user("voter1")
        url = reverse("election-vote-submit", args=[self.election.id])

        voter1 = FreeIPAUser(
            "voter1",
            {
                "uid": ["voter1"],
                "mail": ["voter1@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c2.id, self.c1.id],
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])

        self.assertEqual(Email.objects.count(), 1)
        queued = Email.objects.first()
        assert queued is not None
        self.assertEqual(queued.template.name, "test-vote-receipt")

        ctx = queued.context
        if isinstance(ctx, str):
            ctx = json.loads(ctx)
        self.assertEqual(ctx.get("ballot_hash"), payload.get("ballot_hash"))
        self.assertEqual(ctx.get("nonce"), payload.get("nonce"))

    def test_vote_submit_rejects_invalid_credential(self) -> None:
        self._login_as_freeipa_user("voter1")

        url = reverse("election-vote-submit", args=[self.election.id])

        voter1 = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": "nope",
                    "ranking": [self.c1.id],
                },
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_vote_submit_rejects_when_election_not_open(self) -> None:
        self._login_as_freeipa_user("voter1")

        self.election.status = Election.Status.closed
        self.election.save(update_fields=["status"])

        url = reverse("election-vote-submit", args=[self.election.id])

        voter1 = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c1.id],
                },
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_vote_submit_rejects_when_user_not_eligible(self) -> None:
        self._login_as_freeipa_user("ineligible")

        url = reverse("election-vote-submit", args=[self.election.id])

        ineligible = FreeIPAUser("ineligible", {"uid": ["ineligible"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=ineligible):
            response = self.client.post(
                url,
                data={
                    "credential_public_id": "nope",
                    "ranking": [self.c1.id],
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    @override_settings(
        ELECTION_RATE_LIMIT_VOTE_SUBMIT_LIMIT=1,
        ELECTION_RATE_LIMIT_VOTE_SUBMIT_WINDOW_SECONDS=60 * 60,
    )
    def test_vote_submit_rate_limits_repeated_submissions(self) -> None:
        cache.clear()
        self._login_as_freeipa_user("voter1")

        url = reverse("election-vote-submit", args=[self.election.id])

        voter1 = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            resp1 = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c2.id, self.c1.id],
                },
                content_type="application/json",
            )
        self.assertEqual(resp1.status_code, 200)

        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            resp2 = self.client.post(
                url,
                data={
                    "credential_public_id": self.cred.public_id,
                    "ranking": [self.c2.id, self.c1.id],
                },
                content_type="application/json",
            )
        self.assertEqual(resp2.status_code, 429)
        self.assertIn("error", resp2.json())


class ElectionPublicPagesTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        self._coc_patcher = patch("core.views_elections.vote.block_action_without_coc", return_value=None)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)

    def test_elections_list_and_detail_pages_render(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Public pages election",
            description="Hello",
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

        list_url = reverse("elections")
        detail_url = reverse("election-detail", args=[election.id])

        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})

        def _get_user(username: str):
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            if username == "viewer":
                return viewer
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp1 = self.client.get(list_url)
            self.assertEqual(resp1.status_code, 200)
            resp2 = self.client.get(detail_url)
        self.assertEqual(resp2.status_code, 200)

    def test_vote_page_only_available_when_open(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Vote page election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        vote_url = reverse("election-vote", args=[election.id])

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            self.assertEqual(self.client.get(vote_url).status_code, 200)

        election.status = Election.Status.closed
        election.save(update_fields=["status"])
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            self.assertEqual(self.client.get(vote_url).status_code, 410)

        election.status = Election.Status.tallied
        election.save(update_fields=["status"])
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            self.assertEqual(self.client.get(vote_url).status_code, 410)

    def test_vote_page_does_not_prefill_credential_from_query_param(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Vote page prefill election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        vote_url = reverse("election-vote", args=[election.id])

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(vote_url + "?credential=cred-xyz")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="cred-xyz"')

    def test_vote_page_disables_submit_when_not_eligible(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Vote page eligibility election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        vote_url = reverse("election-vote", args=[election.id])

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(vote_url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "You do not appear to be eligible")
        self.assertContains(resp, "Submit vote")
        self.assertContains(resp, "disabled")

    def test_vote_page_has_receipt_box_and_submit_button_ids(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Vote page ui contract election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        vote_url = reverse("election-vote", args=[election.id])

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(vote_url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="election-receipt-box"')
        self.assertContains(resp, 'class="d-none" id="election-receipt-box"')
        self.assertContains(resp, 'id="election-submit-button"')

    def test_vote_page_randomizes_candidate_order(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Vote page randomization election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

        vote_url = reverse("election-vote", args=[election.id])

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            if username == "nominator":
                return nominator
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("random.shuffle") as shuffle_mock,
        ):
            resp = self.client.get(vote_url)

        self.assertEqual(resp.status_code, 200)
        shuffle_mock.assert_called_once()

        arg0 = shuffle_mock.call_args[0][0]
        self.assertEqual({int(c.id) for c in arg0}, {int(c1.id), int(c2.id)})

    def test_election_detail_actions_shows_not_eligible_message(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        nominator = FreeIPAUser(
            "nominator",
            {"uid": ["nominator"], "displayname": ["Nominator User"], "memberof_group": []},
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Detail eligibility election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        detail_url = reverse("election-detail", args=[election.id])

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(detail_url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "not eligible")
        self.assertContains(resp, "/membership/request/")


