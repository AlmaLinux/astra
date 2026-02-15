import datetime

from django.test import TestCase
from django.utils import timezone

from core.models import Ballot, Candidate, Election
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class BallotQuerySSOTTests(TestCase):
    def test_final_and_latest_chain_head_query_primitives(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Ballot query ssot",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash_1 = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            nonce="n1",
        )
        chain_hash_1 = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash_1)
        ballot_1 = Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            ballot_hash=ballot_hash_1,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash_1,
            is_counted=False,
        )

        final_ballot_hashes = set(Ballot.objects.for_election(election=election).final().values_list("ballot_hash", flat=True))
        self.assertEqual(final_ballot_hashes, {ballot_1.ballot_hash})

        self.assertEqual(
            Ballot.objects.latest_chain_head_hash_for_election(election=election),
            ballot_1.chain_hash,
        )
