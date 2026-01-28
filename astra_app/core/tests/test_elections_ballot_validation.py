from __future__ import annotations

import datetime
import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import Ballot, Candidate, Election, VotingCredential


class ElectionBallotValidationTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_vote_submit_rejects_duplicate_candidates(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Dup test",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )

        self._login_as_freeipa_user("voter1")
        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "voter1",
                {
                    "uid": ["voter1"],
                    "mail": [],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                data=json.dumps({"credential_public_id": "cred-1", "ranking": [c1.id, c1.id]}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Ballot.objects.filter(election=election).count(), 0)

    def test_vote_submit_rejects_candidates_not_in_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Invalid candidate test",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        other = Election.objects.create(
            name="Other election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )
        other_candidate = Candidate.objects.create(
            election=other,
            freeipa_username="mallory",
            nominated_by="nominator",
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )

        self._login_as_freeipa_user("voter1")
        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "voter1",
                {
                    "uid": ["voter1"],
                    "mail": [],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                data=json.dumps({"credential_public_id": "cred-1", "ranking": [c1.id, other_candidate.id]}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Ballot.objects.filter(election=election).count(), 0)

    def test_vote_submit_rejects_unknown_username_in_no_js_fallback(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Username fallback test",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )

        self._login_as_freeipa_user("voter1")
        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "voter1",
                {
                    "uid": ["voter1"],
                    "mail": [],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                {"credential_public_id": "cred-1", "ranking_usernames": "alice,notacandidate"},
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Ballot.objects.filter(election=election).count(), 0)

    def test_vote_submit_requires_signed_coc(self) -> None:
        from core.backends import FreeIPAFASAgreement

        now = timezone.now()
        election = Election.objects.create(
            name="CoC vote",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
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
        voter = FreeIPAUser(
            "voter1",
            {
                "uid": ["voter1"],
                "mail": [],
                "memberof_group": [],
                "memberofindirect_group": [],
            },
        )
        with patch("core.backends.FreeIPAUser.get", autospec=True, return_value=voter):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.post(
                    reverse("election-vote-submit", args=[election.id]),
                    data=json.dumps({"credential_public_id": "cred-1", "ranking": [c1.id]}),
                    content_type="application/json",
                )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Ballot.objects.filter(election=election).count(), 0)
