
import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.elections_services import election_quorum_status
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class ElectionQuorumAuditTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._coc_patcher = patch("core.views_elections.vote.has_signed_coc", return_value=True)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def test_quorum_reached_logged_once_when_threshold_met(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum test",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=100,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-2",
            freeipa_username="voter2",
            weight=1,
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        m1 = Membership.objects.create(target_username="voter1", membership_type=voter_type, expires_at=None)
        m2 = Membership.objects.create(target_username="voter2", membership_type=voter_type, expires_at=None)
        eligible_created_at = election.start_datetime - datetime.timedelta(days=2)
        Membership.objects.filter(pk=m1.pk).update(created_at=eligible_created_at)
        Membership.objects.filter(pk=m2.pk).update(created_at=eligible_created_at)

        self._login_as_freeipa_user("voter1")
        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "voter1",
                {
                    "uid": ["voter1"],
                    "mail": ["voter1@example.com"],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                {"credential_public_id": "cred-1", "ranking_usernames": "alice"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AuditLogEntry.objects.filter(election=election, event_type="quorum_reached").exists())

        self._login_as_freeipa_user("voter2")
        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "voter2",
                {
                    "uid": ["voter2"],
                    "mail": ["voter2@example.com"],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                {"credential_public_id": "cred-2", "ranking_usernames": "alice"},
            )
        self.assertEqual(resp.status_code, 200)

        reached = list(AuditLogEntry.objects.filter(election=election, event_type="quorum_reached"))
        self.assertEqual(len(reached), 1)
        self.assertTrue(reached[0].is_public)

        # Submitting again should not create duplicates.
        self._login_as_freeipa_user("voter2")
        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "voter2",
                {
                    "uid": ["voter2"],
                    "mail": ["voter2@example.com"],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                {"credential_public_id": "cred-2", "ranking_usernames": "alice"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AuditLogEntry.objects.filter(election=election, event_type="quorum_reached").count(), 1)

    def test_open_election_end_extension_logs_audit_event(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Extend test",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")

        with patch("core.backends.FreeIPAUser.get") as mocked_get:
            mocked_get.return_value = FreeIPAUser(
                "admin",
                {
                    "uid": ["admin"],
                    "mail": ["admin@example.com"],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )

            new_end = now + datetime.timedelta(days=2)
            resp = self.client.post(
                reverse("election-edit", args=[election.id]),
                {"action": "extend_end", "end_datetime": new_end.strftime("%Y-%m-%dT%H:%M")},
            )
        self.assertEqual(resp.status_code, 302)

        election.refresh_from_db()
        self.assertGreater(election.end_datetime, now + datetime.timedelta(days=1))

        entries = list(AuditLogEntry.objects.filter(election=election, event_type="election_end_extended"))
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].is_public)
        payload = entries[0].payload if isinstance(entries[0].payload, dict) else {}
        self.assertIn("previous_end_datetime", payload)
        self.assertIn("new_end_datetime", payload)
        self.assertIn("quorum_percent", payload)


class ElectionQuorumStatusTests(TestCase):
    def _create_ballot(self, *, election: Election, credential_public_id: str, weight: int) -> None:
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id=credential_public_id,
            ranking=[],
            weight=weight,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id=credential_public_id,
            ranking=[],
            weight=weight,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

    def test_quorum_requires_weight_and_count_thresholds(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum dual threshold",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-2",
            freeipa_username="voter2",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-3",
            freeipa_username="voter3",
            weight=3,
        )

        self._create_ballot(election=election, credential_public_id="cred-1", weight=1)
        self._create_ballot(election=election, credential_public_id="cred-2", weight=1)

        status = election_quorum_status(election=election)
        self.assertEqual(status.get("required_participating_voter_count"), 2)
        self.assertEqual(status.get("required_participating_vote_weight_total"), 3)
        self.assertFalse(status.get("quorum_met"))

    def test_quorum_met_when_both_thresholds_satisfied(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum dual threshold met",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-2",
            freeipa_username="voter2",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-3",
            freeipa_username="voter3",
            weight=3,
        )

        self._create_ballot(election=election, credential_public_id="cred-1", weight=1)
        self._create_ballot(election=election, credential_public_id="cred-3", weight=3)

        status = election_quorum_status(election=election)
        self.assertEqual(status.get("required_participating_voter_count"), 2)
        self.assertEqual(status.get("required_participating_vote_weight_total"), 3)
        self.assertTrue(status.get("quorum_met"))

    def test_quorum_not_required_when_percent_zero(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum not required",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )

        status = election_quorum_status(election=election)
        self.assertFalse(status.get("quorum_required"))
        self.assertEqual(status.get("required_participating_voter_count"), 0)
        self.assertEqual(status.get("required_participating_vote_weight_total"), 0)

    def test_quorum_not_met_when_no_ballots_cast(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum no ballots",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-2",
            freeipa_username="voter2",
            weight=1,
        )

        status = election_quorum_status(election=election)
        self.assertEqual(status.get("participating_voter_count"), 0)
        self.assertEqual(status.get("participating_vote_weight_total"), 0)
        self.assertFalse(status.get("quorum_met"))

    def test_quorum_not_met_when_only_weight_threshold_satisfied(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum weight only",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=50,
            status=Election.Status.open,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-2",
            freeipa_username="voter2",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-3",
            freeipa_username="voter3",
            weight=10,
        )

        # With 3 eligible voters and quorum 50%: require 2 voters.
        # With total vote weight 12 and quorum 50%: require 6 vote weight.
        self._create_ballot(election=election, credential_public_id="cred-3", weight=10)

        status = election_quorum_status(election=election)
        self.assertEqual(status.get("required_participating_voter_count"), 2)
        self.assertEqual(status.get("required_participating_vote_weight_total"), 6)
        self.assertFalse(status.get("quorum_met"))

    def test_quorum_thresholds_use_ceiling(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum ceil",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=34,
            status=Election.Status.open,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-2",
            freeipa_username="voter2",
            weight=1,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-3",
            freeipa_username="voter3",
            weight=1,
        )

        status = election_quorum_status(election=election)
        # ceil(3 * 34 / 100) == 2
        self.assertEqual(status.get("required_participating_voter_count"), 2)
        # ceil(3 * 34 / 100) == 2
        self.assertEqual(status.get("required_participating_vote_weight_total"), 2)
