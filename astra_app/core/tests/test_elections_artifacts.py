
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import elections_services
from core.models import Ballot, Candidate, Election
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class ElectionArtifactGenerationTests(TestCase):
    def test_build_public_ballots_export_uses_candidate_usernames_in_rankings(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact election (ranking mapping)",
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

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
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

        payload = elections_services.build_public_ballots_export(election=election)
        self.assertEqual(payload["ballots"][0]["ranking"], ["alice"])

    def test_tally_generates_public_ballots_and_audit_artifacts(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact election",
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

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
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
        self.assertTrue(str(election.public_ballots_file.name or "").strip())
        self.assertTrue(str(election.public_audit_file.name or "").strip())
        self.assertIn(f"elections/{election.id}/", election.public_ballots_file.name)
        self.assertIn(f"elections/{election.id}/", election.public_audit_file.name)

    def test_public_export_endpoints_redirect_to_stored_artifacts_when_tallied(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact endpoints election",
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

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
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

        ballots_resp = self.client.get(reverse("election-public-ballots", args=[election.id]))
        self.assertEqual(ballots_resp.status_code, 302)
        self.assertIn(f"/elections/{election.id}/", str(ballots_resp["Location"]))

        audit_resp = self.client.get(reverse("election-public-audit", args=[election.id]))
        self.assertEqual(audit_resp.status_code, 302)
        self.assertIn(f"/elections/{election.id}/", str(audit_resp["Location"]))
