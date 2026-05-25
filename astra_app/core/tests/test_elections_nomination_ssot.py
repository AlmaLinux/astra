import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.forms_elections import (
    CandidateWizardForm,
    CandidateWizardFormSet,
    ElectionDetailsForm,
    ExclusionGroupWizardForm,
    ExclusionGroupWizardFormSet,
)
from core.models import Candidate, Election, ExclusionGroup


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

    def test_election_details_form_rejects_manifest_backed_changes_for_started_v2_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Started v2",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            chain_version=2,
        )

        form = ElectionDetailsForm(
            data={
                "name": "Tampered",
                "description": "",
                "url": "",
                "start_datetime": election.start_datetime.strftime("%Y-%m-%dT%H:%M"),
                "end_datetime": election.end_datetime.strftime("%Y-%m-%dT%H:%M"),
                "number_of_seats": 1,
                "quorum": 10,
                "eligible_group_cn": "",
            },
            instance=election,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_exclusion_group_form_rejects_manifest_backed_changes_for_started_v2_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Started v2",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
            chain_version=2,
        )
        group = ExclusionGroup.objects.create(election=election, name="Employees", max_elected=1)
        election.status = Election.Status.open
        election.save(update_fields=["status", "updated_at"])

        form = ExclusionGroupWizardForm(
            data={
                "name": "Employees",
                "max_elected": 2,
                "candidate_usernames": [],
            },
            instance=group,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("max_elected", form.errors)

    def test_candidate_formset_validation_allows_new_row_before_election_assignment(self) -> None:
        formset = CandidateWizardFormSet(
            data={
                "candidates-TOTAL_FORMS": "1",
                "candidates-INITIAL_FORMS": "0",
                "candidates-MIN_NUM_FORMS": "0",
                "candidates-MAX_NUM_FORMS": "1000",
                "candidates-0-id": "",
                "candidates-0-freeipa_username": "alice",
                "candidates-0-nominated_by": "bob",
                "candidates-0-description": "",
                "candidates-0-url": "",
                "candidates-0-DELETE": "",
            },
            queryset=Candidate.objects.none(),
            prefix="candidates",
        )

        for form in formset.forms:
            form.fields["freeipa_username"].choices = [("alice", "alice")]
            form.fields["nominated_by"].choices = [("bob", "bob")]

        self.assertTrue(formset.is_valid(), formset.errors)

    def test_exclusion_group_formset_validation_allows_new_row_before_election_assignment(self) -> None:
        formset = ExclusionGroupWizardFormSet(
            data={
                "groups-TOTAL_FORMS": "1",
                "groups-INITIAL_FORMS": "0",
                "groups-MIN_NUM_FORMS": "0",
                "groups-MAX_NUM_FORMS": "1000",
                "groups-0-id": "",
                "groups-0-name": "Employees",
                "groups-0-max_elected": "1",
                "groups-0-candidate_usernames": ["alice"],
                "groups-0-DELETE": "",
            },
            queryset=ExclusionGroup.objects.none(),
            prefix="groups",
        )

        for form in formset.forms:
            form.fields["candidate_usernames"].choices = [("alice", "alice")]

        self.assertTrue(formset.is_valid(), formset.errors)
