from unittest.mock import patch

from django.test import Client, TestCase

from core.backends import FreeIPAUser
from core.models import AccountInvitation, Organization


class LoginInviteTokenSemanticsTests(TestCase):
    def test_login_invite_flow_uses_reconcile_helpers(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            },
        )

        with (
            patch("django.contrib.auth.forms.authenticate", return_value=user),
            patch("core.views_auth.load_account_invitation_from_token", create=True, return_value=invitation) as load_mock,
            patch("core.views_auth.reconcile_account_invitation_for_username", create=True) as reconcile_mock,
        ):
            resp = client.post(
                f"/login/?invite={token}",
                data={
                    "username": "alice",
                    "password": "pw",
                    "invite": token,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        load_mock.assert_called_once_with(token)
        self.assertEqual(reconcile_mock.call_count, 1)
        self.assertEqual(reconcile_mock.call_args.kwargs["invitation"], invitation)
        self.assertEqual(reconcile_mock.call_args.kwargs["username"], "alice")

    def test_login_with_non_org_invite_marks_invitation_accepted(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            },
        )

        with patch("django.contrib.auth.forms.authenticate", return_value=user):
            resp = client.post(
                f"/login/?invite={token}",
                data={
                    "username": "alice",
                    "password": "pw",
                    "invite": token,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)

    def test_login_with_org_linked_invite_keeps_invitation_pending(self) -> None:
        client = Client()

        organization = Organization.objects.create(
            name="Pending Claim Org",
            business_contact_email="invitee@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
            organization=organization,
        )
        token = str(invitation.invitation_token)

        user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            },
        )

        with patch("django.contrib.auth.forms.authenticate", return_value=user):
            resp = client.post(
                f"/login/?invite={token}",
                data={
                    "username": "alice",
                    "password": "pw",
                    "invite": token,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)
