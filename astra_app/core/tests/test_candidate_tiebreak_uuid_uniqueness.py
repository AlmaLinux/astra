import datetime
import uuid

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from core.models import Candidate, Election


class CandidateTiebreakUUIDUniquenessTests(TestCase):
    def test_tiebreak_uuid_must_be_unique_within_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="UUID uniqueness election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.draft,
        )
        shared_uuid = uuid.uuid4()

        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="reviewer",
            tiebreak_uuid=shared_uuid,
        )

        with self.assertRaises(IntegrityError):
            Candidate.objects.create(
                election=election,
                freeipa_username="bob",
                nominated_by="reviewer",
                tiebreak_uuid=shared_uuid,
            )
