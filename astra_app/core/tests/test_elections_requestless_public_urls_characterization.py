import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.elections_services import ballot_verify_url, election_vote_url
from core.models import Election


class ElectionsRequestlessPublicUrlsCharacterizationTests(TestCase):
    def _create_election(self) -> Election:
        now = timezone.now()
        return Election.objects.create(
            name="Characterization Election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

    @override_settings(PUBLIC_BASE_URL="")
    def test_election_vote_url_without_request_and_empty_public_base_returns_relative_path(self) -> None:
        election = self._create_election()

        self.assertEqual(
            election_vote_url(request=None, election=election),
            reverse("election-vote", args=[election.pk]),
        )

    @override_settings(PUBLIC_BASE_URL="https://example.com/")
    def test_election_vote_url_without_request_and_public_base_returns_absolute_url(self) -> None:
        election = self._create_election()

        self.assertEqual(
            election_vote_url(request=None, election=election),
            "https://example.com" + reverse("election-vote", args=[election.pk]),
        )

    @override_settings(PUBLIC_BASE_URL="")
    def test_ballot_verify_url_without_request_and_empty_public_base_returns_relative_url(self) -> None:
        self.assertEqual(
            ballot_verify_url(request=None, ballot_hash="abc"),
            reverse("ballot-verify") + "?receipt=abc",
        )

    @override_settings(PUBLIC_BASE_URL="https://example.com/")
    def test_ballot_verify_url_without_request_and_public_base_returns_absolute_url(self) -> None:
        self.assertEqual(
            ballot_verify_url(request=None, ballot_hash="abc"),
            "https://example.com" + (reverse("ballot-verify") + "?receipt=abc"),
        )
