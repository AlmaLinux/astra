
import datetime
from unittest.mock import patch
from urllib.parse import quote

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import Candidate, Election


class ElectionsSidebarLinkTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_sidebar_includes_elections_link(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "memberof_group": [],
            },
        )
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("user-profile", args=["viewer"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'href="{reverse("elections")}"')
        self.assertContains(resp, ">Elections<")


class ElectionsVoteAccessTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_vote_page_requires_signed_coc(self) -> None:
        from core.backends import FreeIPAFASAgreement

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        coc = FreeIPAFASAgreement(
            settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN,
            {
                "cn": [settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN],
                "ipaenabledflag": ["TRUE"],
                "memberuser_user": [],
            },
        )

        self._login_as_freeipa_user("voter1")
        voter = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", autospec=True, return_value=voter):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.get(reverse("election-vote", args=[election.id]), follow=False)

        self.assertEqual(resp.status_code, 302)
        expected = (
            f"{reverse('settings')}?agreement={quote(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}#agreements"
        )
        self.assertEqual(resp["Location"], expected)


class ElectionsDetailCandidateCardsTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_election_detail_shows_candidate_cards_with_nominator_and_urls(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="Elect the board",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
            url="https://example.com/elections/board-2026",
        )

        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            description="A short bio.",
            url="https://example.com/~alice",
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "memberof_group": [],
            },
        )
        nominator = FreeIPAUser(
            "nominator",
            {
                "uid": ["nominator"],
                "givenname": ["Nominator"],
                "sn": ["Person"],
                "displayname": ["Nominator Person"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return nominator
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, election.url)
        self.assertContains(resp, candidate.url)

        self.assertContains(resp, reverse("user-profile", args=["alice"]))
        self.assertContains(resp, "Alice User")
        self.assertContains(resp, "A short bio")

        self.assertContains(resp, "Nominated by")
        self.assertContains(resp, reverse("user-profile", args=["nominator"]))
        self.assertContains(resp, "Nominator Person")
