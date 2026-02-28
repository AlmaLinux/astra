import datetime

from django.test import TestCase
from django.utils import timezone

from core.models import AuditLogEntry, Ballot, Candidate, Election, VotingCredential


class ElectionDeletedCleanupSignalTests(TestCase):
    def _create_election(self) -> Election:
        now = timezone.now()
        return Election.objects.create(
            name="Election to soft-delete",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

    def test_soft_delete_removes_voting_credentials_only(self) -> None:
        election = self._create_election()
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            ballot_hash="a" * 64,
            previous_chain_hash="b" * 64,
            chain_hash="c" * 64,
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="seed_event",
            payload={"ok": True},
            is_public=True,
        )

        election.status = Election.Status.deleted
        election.save(update_fields=["status"])

        self.assertFalse(VotingCredential.objects.filter(election=election).exists())
        self.assertTrue(Ballot.objects.filter(election=election).exists())
        self.assertTrue(AuditLogEntry.objects.filter(election=election, event_type="seed_event").exists())
