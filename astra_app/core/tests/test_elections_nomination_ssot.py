import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.forms_elections import CandidateWizardForm
from core.models import Candidate, Election


class ElectionsNominationSSOTTests(TestCase):
    def test_candidate_form_uses_self_nomination_ssot_validator(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Nomination ssot",
            description="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        form = CandidateWizardForm(
            data={
                "freeipa_username": "alice",
                "nominated_by": "alice",
                "description": "",
                "url": "",
            },
            instance=Candidate(election=election),
        )
        form.fields["freeipa_username"].choices = [("alice", "alice")]
        form.fields["nominated_by"].choices = [("alice", "alice")]

        with patch("core.forms_elections.is_self_nomination") as validator_mock:
            validator_mock.return_value = True
            self.assertFalse(form.is_valid())

        validator_mock.assert_called_once_with(candidate_username="alice", nominator_username="alice")
