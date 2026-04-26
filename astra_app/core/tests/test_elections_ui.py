
import datetime
from unittest.mock import patch
from urllib.parse import quote_plus

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import Election


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
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
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
        from core.freeipa.agreement import FreeIPAFASAgreement

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
        with patch("core.freeipa.user.FreeIPAUser.get", autospec=True, return_value=voter):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.get(reverse("election-vote", args=[election.id]), follow=False)

        self.assertEqual(resp.status_code, 302)
        expected = (
            f"{reverse('settings')}?tab=agreements&agreement={quote_plus(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}"
        )
        location = str(resp["Location"])
        self.assertTrue(location.startswith(expected))
        self.assertIn("return=", location)


class ElectionsListShellTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_elections_list_returns_vue_shell_without_querying_rows(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        Election.objects.create(
            name="Rendered server election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch("core.views_elections.detail._visible_elections_queryset", return_value=[]) as visible_elections,
        ):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        visible_elections.assert_not_called()
        self.assertContains(resp, 'data-elections-root')
        self.assertContains(resp, reverse("api-elections"))
        self.assertNotContains(resp, "Rendered server election")


class ElectionsVoteShellTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_election_vote_returns_vue_shell_without_server_side_ballot_data(self) -> None:
        self._login_as_freeipa_user("voter")

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        voter = FreeIPAUser("voter", {"uid": ["voter"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=voter),
            patch("core.views_elections.vote.block_action_without_coc", return_value=None),
            patch(
                "core.views_elections.vote._election_vote_page_context",
                return_value={
                    "election": election,
                    "candidates": [],
                    "voter_votes": None,
                    "voter_vote_breakdown": [],
                    "can_submit_vote": False,
                },
            ) as page_context,
        ):
            resp = self.client.get(reverse("election-vote", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        page_context.assert_not_called()
        self.assertContains(resp, 'data-election-vote-root')
        self.assertContains(resp, reverse("api-election-vote", args=[election.id]))
        self.assertContains(resp, 'data-election-vote-detail-url-template="/elections/__election_id__/"')
        self.assertContains(resp, f'data-election-vote-verify-url="{reverse("ballot-verify")}"')
        self.assertNotContains(resp, "Voting window")
        self.assertNotContains(resp, "Candidates")


class ElectionsDetailShellTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_election_detail_returns_vue_shell_without_server_side_page_data(self) -> None:
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

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch("core.views_elections.detail._candidate_cards_context") as candidate_cards_context,
            patch("core.views_elections.detail._election_detail_summary_context") as summary_context,
            patch("core.views_elections.detail._eligible_voters_context") as eligible_voters_context,
            patch("core.views_elections.detail._vote_access_context") as vote_access_context,
        ):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        candidate_cards_context.assert_not_called()
        summary_context.assert_not_called()
        eligible_voters_context.assert_not_called()
        vote_access_context.assert_not_called()
        self.assertContains(resp, 'data-election-detail-root')
        self.assertContains(resp, reverse("api-election-detail-info", args=[election.id]))
        self.assertContains(resp, reverse("api-election-detail-candidates", args=[election.id]))
        self.assertNotContains(resp, "data-election-detail-info-json-id")
        self.assertNotContains(resp, "election-detail-initial-info")
        self.assertNotContains(resp, "candidate-card-divider")
        self.assertNotContains(resp, "Elect the board")
        self.assertNotContains(resp, "https://example.com/elections/board-2026")
