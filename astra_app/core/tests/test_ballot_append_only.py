
import datetime

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from core.elections_services import submit_ballot
from core.models import Ballot, Candidate, Election, VotingCredential


class BallotAppendOnlyTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        now = timezone.now()
        self.election = Election.objects.create(
            name="Append-only ballot election",
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
            public_id="cred-append-only",
            freeipa_username="voter1",
            weight=1,
        )

    def test_ballot_rows_cannot_be_deleted(self) -> None:
        receipt = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id, self.c2.id],
        )
        ballot = receipt.ballot

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                ballot.delete()

    def test_ballot_rows_cannot_be_updated_except_superseded_by_and_is_counted(self) -> None:
        receipt = submit_ballot(
            election=self.election,
            credential_public_id=self.cred.public_id,
            ranking=[self.c1.id, self.c2.id],
        )
        ballot = receipt.ballot

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Ballot.objects.filter(pk=ballot.pk).update(ranking=[self.c2.id, self.c1.id])

        Ballot.objects.filter(pk=ballot.pk).update(is_counted=False)

    def test_superseded_by_must_point_forward(self) -> None:
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

        # The normal direction (ballot1 -> ballot2) is created by submit_ballot.
        ballot1.refresh_from_db(fields=["superseded_by_id"])
        self.assertEqual(ballot1.superseded_by_id, ballot2.id)

        # Backdating / cycling should be rejected.
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Ballot.objects.filter(pk=ballot2.pk).update(superseded_by=ballot1)
                # `core_ballot_validate_supersession_trg` is DEFERRABLE INITIALLY DEFERRED.
                # In Django TestCase, the outer transaction doesn't commit, so force evaluation here.
                connection.check_constraints()
