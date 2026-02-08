
import datetime
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import Ballot, Candidate, Election, VotingCredential
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class AdvanceElectionsCommandTests(TestCase):
    def test_advance_elections_closes_and_tallies_open_election_past_end(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            now = timezone.now()
            election = Election.objects.create(
                name="Auto close+tally",
                description="",
                start_datetime=now - datetime.timedelta(days=10),
                end_datetime=now - datetime.timedelta(minutes=1),
                number_of_seats=1,
                status=Election.Status.open,
            )
            c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
            c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

            VotingCredential.objects.create(
                election=election,
                public_id="cred-1",
                freeipa_username="voter1",
                weight=1,
            )
            genesis_hash = election_genesis_chain_hash(election.id)
            ballot_hash = Ballot.compute_hash(
                election_id=election.id,
                credential_public_id="cred-1",
                ranking=[c1.id, c2.id],
                weight=1,
                nonce="0" * 32,
            )
            chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
            Ballot.objects.create(
                election=election,
                credential_public_id="cred-1",
                ranking=[c1.id, c2.id],
                weight=1,
                ballot_hash=ballot_hash,
                previous_chain_hash=genesis_hash,
                chain_hash=chain_hash,
            )

            call_command("advance_elections")

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertIsNotNone(election.tally_result)
        self.assertIsNone(VotingCredential.objects.get(election=election, public_id="cred-1").freeipa_username)

    def test_advance_elections_tallies_closed_election_past_end(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            now = timezone.now()
            election = Election.objects.create(
                name="Auto tally",
                description="",
                start_datetime=now - datetime.timedelta(days=10),
                end_datetime=now - datetime.timedelta(days=1),
                number_of_seats=1,
                status=Election.Status.closed,
            )
            c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
            genesis_hash = election_genesis_chain_hash(election.id)
            ballot_hash = Ballot.compute_hash(
                election_id=election.id,
                credential_public_id="cred-x",
                ranking=[c1.id],
                weight=1,
                nonce="0" * 32,
            )
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

            call_command("advance_elections")

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertIsNotNone(election.tally_result)
