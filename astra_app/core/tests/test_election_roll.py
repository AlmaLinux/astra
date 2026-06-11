"""Tests for the ElectionRoll model and its integration with election lifecycle."""

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.elections_services import issue_credentials_at_start_transition
from core.freeipa.user import FreeIPAUser
from core.models import (
    Election,
    ElectionRoll,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.utils_test_data import ensure_core_categories


class _CoreCategoriesTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()


class ElectionRollPopulationTests(_CoreCategoriesTestCase):
    """ElectionRoll must be populated from VotingCredential at credential issuance time."""

    def _make_election_and_members(self, *, usernames: list[str]) -> Election:
        now = timezone.now()
        election = Election.objects.create(
            name="Roll test election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        voter_type = MembershipType.objects.create(
            code="roll-voter",
            name="Roll Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        for username in usernames:
            m = Membership.objects.create(
                target_username=username,
                membership_type=voter_type,
                expires_at=None,
            )
            Membership.objects.filter(pk=m.pk).update(
                created_at=election.start_datetime - datetime.timedelta(days=200),
            )
        return election

    def test_issue_credentials_populates_election_roll(self) -> None:
        usernames = ["alice", "bob", "carol"]
        election = self._make_election_and_members(usernames=usernames)

        credentials = issue_credentials_at_start_transition(election=election)

        self.assertEqual(len(credentials), 3)

        roll_usernames = set(
            ElectionRoll.objects.filter(election=election).values_list("freeipa_username", flat=True)
        )
        self.assertEqual(roll_usernames, {"alice", "bob", "carol"})

    def test_election_roll_survives_anonymization(self) -> None:
        """After anonymize_election nulls credential usernames, the roll is intact."""
        usernames = ["alice", "bob"]
        election = self._make_election_and_members(usernames=usernames)

        issue_credentials_at_start_transition(election=election)

        # Simulate anonymization (what close_election does).
        VotingCredential.objects.filter(election=election).update(freeipa_username=None)

        # Credentials have no usernames, but roll does.
        self.assertFalse(
            VotingCredential.objects.filter(election=election)
            .exclude(freeipa_username__isnull=True)
            .exists()
        )
        roll_usernames = set(
            ElectionRoll.objects.filter(election=election).values_list("freeipa_username", flat=True)
        )
        self.assertEqual(roll_usernames, {"alice", "bob"})

    def test_election_roll_matches_credentials_exactly(self) -> None:
        """The roll must contain exactly the same usernames as the credentials."""
        usernames = ["alice", "bob", "carol"]
        election = self._make_election_and_members(usernames=usernames)

        credentials = issue_credentials_at_start_transition(election=election)

        credential_usernames = {str(c.freeipa_username) for c in credentials}
        roll_usernames = set(
            ElectionRoll.objects.filter(election=election).values_list("freeipa_username", flat=True)
        )
        self.assertEqual(roll_usernames, credential_usernames)

    def test_election_roll_order_differs_from_credentials(self) -> None:
        """The roll insertion order must not match credential insertion order.

        _populate_election_roll uses ORDER BY RANDOM() so that positional
        correlation between the roll and the (later anonymized) credentials
        cannot be used to deanonymize voters.  With 15 users the probability
        of an accidental match is 1/15! ≈ 10⁻¹².
        """
        usernames = [f"voter{i:02d}" for i in range(15)]
        election = self._make_election_and_members(usernames=usernames)

        issue_credentials_at_start_transition(election=election)

        # Credential insertion order (by ascending id).
        credential_order = list(
            VotingCredential.objects.filter(election=election)
            .order_by("id")
            .values_list("freeipa_username", flat=True)
        )
        # Roll insertion order (by ascending id).
        roll_order = list(
            ElectionRoll.objects.filter(election=election)
            .order_by("id")
            .values_list("freeipa_username", flat=True)
        )

        self.assertEqual(set(credential_order), set(roll_order))
        self.assertNotEqual(
            credential_order,
            roll_order,
            "Roll order must differ from credential order to prevent deanonymization.",
        )


class ElectionRollEligibleVotersApiTests(_CoreCategoriesTestCase):
    """The eligible voters API must use ElectionRoll for closed/tallied elections."""

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_closed_election_eligible_voters_from_roll(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Closed roll election",
            description="",
            start_datetime=now - datetime.timedelta(days=5),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        # Populate the roll with historical voters (alice, bob).
        ElectionRoll.objects.create(election=election, freeipa_username="alice")
        ElectionRoll.objects.create(election=election, freeipa_username="bob")

        # Make a *new* membership for carol (who was NOT eligible at election time).
        voter_type = MembershipType.objects.create(
            code="roll-voter-2",
            name="Roll Voter 2",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        m = Membership.objects.create(
            target_username="carol",
            membership_type=voter_type,
            expires_at=None,
        )
        Membership.objects.filter(pk=m.pk).update(
            created_at=election.start_datetime - datetime.timedelta(days=200),
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice"], "mail": ["alice@example.com"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob"], "mail": ["bob@example.com"], "memberof_group": []})
        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "displayname": ["Admin"], "mail": ["admin@example.com"], "memberof_group": []})

        def _get_user(username, **_kw):
            return {"alice": alice, "bob": bob, "admin": admin_user}.get(username)

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("api-election-detail-eligible-voters", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["eligible_voters"]
        # carol should NOT appear (she's not on the roll even though she has a membership).
        returned_usernames = set(payload["usernames"])
        self.assertIn("alice", returned_usernames)
        self.assertIn("bob", returned_usernames)
        self.assertNotIn("carol", returned_usernames)


class ElectionRollSendEmailTests(_CoreCategoriesTestCase):
    """The email send API must use ElectionRoll for closed/tallied elections."""

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_closed_election_send_email_uses_roll(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Closed send election",
            description="",
            start_datetime=now - datetime.timedelta(days=5),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        # Only alice on the roll.
        ElectionRoll.objects.create(election=election, freeipa_username="alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice"], "mail": ["alice@example.com"], "memberof_group": []})
        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "displayname": ["Admin"], "mail": ["admin@example.com"], "memberof_group": []})

        def _get_user(username, **_kw):
            return {"alice": alice, "admin": admin_user}.get(username)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.elections_services.send_voting_credential_email") as mock_send,
        ):
            resp = self.client.post(
                reverse("api-election-send-mail-credentials", args=[election.id]),
                data={"username": ""},
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["recipient_count"], 1)
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        self.assertEqual(call_kwargs.kwargs["username"], "alice")
        self.assertEqual(call_kwargs.kwargs["include_credentials"], False)

    def test_closed_election_send_email_rejects_user_not_on_roll(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Closed reject election",
            description="",
            start_datetime=now - datetime.timedelta(days=5),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        # Only alice on the roll, but we try to send to bob.
        ElectionRoll.objects.create(election=election, freeipa_username="alice")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "displayname": ["Admin"], "mail": ["admin@example.com"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            resp = self.client.post(
                reverse("api-election-send-mail-credentials", args=[election.id]),
                data={"username": "bob"},
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertFalse(payload["ok"])
