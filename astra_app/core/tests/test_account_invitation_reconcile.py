import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.account_invitation_reconcile import (
    load_account_invitation_from_token,
    reconcile_account_invitation_for_username,
)
from core.models import AccountInvitation, Organization


class AccountInvitationReconcileTests(TestCase):
    def test_load_account_invitation_from_token_requires_row_token_match(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        with patch(
            "core.account_invitation_reconcile.read_signed_token_unbounded",
            return_value={"invitation_id": invitation.pk},
        ):
            loaded = load_account_invitation_from_token("forged-token")

        self.assertIsNone(loaded)

    def test_load_account_invitation_from_token_returns_matching_invitation(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        loaded = load_account_invitation_from_token(str(invitation.invitation_token))

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.pk, invitation.pk)

    def test_reconcile_account_invitation_for_username_is_idempotent_for_non_org(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            freeipa_matched_usernames=["zara"],
        )

        first_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)
        second_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 11, 0, 0), datetime.UTC)

        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=first_now)
        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=second_now)

        invitation.refresh_from_db()
        self.assertEqual(invitation.accepted_at, first_now)
        self.assertEqual(invitation.freeipa_last_checked_at, second_now)
        self.assertEqual(invitation.freeipa_matched_usernames, ["alice", "zara"])

    def test_reconcile_account_invitation_for_username_keeps_org_invitation_pending(self) -> None:
        organization = Organization.objects.create(
            name="Pending Claim Org",
            business_contact_email="invitee@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            organization=organization,
        )
        now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)

        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=now)

        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertEqual(invitation.freeipa_last_checked_at, now)
        self.assertEqual(invitation.freeipa_matched_usernames, ["alice"])
