
import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import elections_services
from core.backends import FreeIPAUser
from core.models import AuditLogEntry, Ballot, Candidate, Election, Membership, MembershipType, VotingCredential
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class AdminElectionLifecycleActionTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_action_close_election_closes_and_anonymizes(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Admin close election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )

        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        with patch("core.backends.FreeIPAUser.get", return_value=admin_user):
            url = reverse("admin:core_election_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "close_elections_action",
                    "_selected_action": [str(election.id)],
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)
        self.assertIsNone(VotingCredential.objects.get(election=election).freeipa_username)
        self.assertTrue(
            AuditLogEntry.objects.filter(election=election, event_type="election_closed", is_public=True).exists()
        )

    def test_admin_action_tally_election_tallies_and_logs_public_rounds(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Admin tally election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-x",
            ranking=[c1.id, c2.id],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-x",
            ranking=[c1.id, c2.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        with patch("core.backends.FreeIPAUser.get", return_value=admin_user):
            url = reverse("admin:core_election_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "tally_elections_action",
                    "_selected_action": [str(election.id)],
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertIn("elected", election.tally_result)

        algo = election.tally_result.get("algorithm")
        self.assertIsInstance(algo, dict)
        self.assertEqual(algo.get("name"), "Meek STV (High-Precision Variant)")
        self.assertEqual(algo.get("version"), "1.0")

        completed = AuditLogEntry.objects.filter(
            election=election,
            event_type="tally_completed",
            is_public=True,
        ).order_by("timestamp", "id").last()
        self.assertIsNotNone(completed)
        completed_payload = completed.payload if isinstance(completed.payload, dict) else {}
        completed_algo = completed_payload.get("algorithm")
        self.assertIsInstance(completed_algo, dict)
        self.assertEqual(completed_algo.get("name"), "Meek STV (High-Precision Variant)")
        self.assertEqual(completed_algo.get("version"), "1.0")

        public_export = elections_services.build_public_audit_export(election=election)
        self.assertIsInstance(public_export.get("algorithm"), dict)
        self.assertEqual(public_export.get("algorithm", {}).get("name"), "Meek STV (High-Precision Variant)")
        self.assertEqual(public_export.get("algorithm", {}).get("version"), "1.0")

        self.assertTrue(
            AuditLogEntry.objects.filter(election=election, event_type="tally_round", is_public=True).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(election=election, event_type="tally_completed", is_public=True).exists()
        )

    def test_admin_action_issue_and_email_credentials_from_memberships(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Admin issue+email election",
            description="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.open,
        )

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            description="",
            category_id="individual",
            sort_order=1,
            enabled=True,
            votes=1,
        )
        m = Membership.objects.create(target_username="voter1", membership_type=mt, expires_at=None)
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=120))

        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        voter_user = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": [], "mail": ["voter1@example.com"]})

        def get_user(username: str):
            if username == "alice":
                return admin_user
            if username == "voter1":
                return voter_user
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=get_user),
            patch("post_office.mail.send", autospec=True) as post_office_send_mock,
        ):
            url = reverse("admin:core_election_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "issue_and_email_credentials_from_memberships_action",
                    "_selected_action": [str(election.id)],
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(VotingCredential.objects.filter(election=election, freeipa_username="voter1").exists())
        self.assertEqual(post_office_send_mock.call_count, 1)
        self.assertEqual(post_office_send_mock.call_args.kwargs.get("recipients"), ["voter1@example.com"])

