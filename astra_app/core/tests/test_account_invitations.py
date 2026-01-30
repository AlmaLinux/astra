from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.account_invitations import find_account_invitation_matches
from core.backends import FreeIPAUser
from core.models import AccountInvitation, AccountInvitationSend, FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_MEMBERSHIP


class AccountInvitationFreeIPAServiceTests(TestCase):
    def test_find_account_invitation_matches_returns_sorted_unique(self) -> None:
        response = {
            "count": 2,
            "result": [
                {"uid": ["Bob"]},
                {"uid": "alice"},
                {"uid": ["bob"]},
            ],
        }

        with patch("core.backends._with_freeipa_service_client_retry", return_value=response):
            usernames = find_account_invitation_matches("team@example.com")

        self.assertEqual(usernames, ["alice", "bob"])


class AccountInvitationViewsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _committee_user(self) -> FreeIPAUser:
        return FreeIPAUser(
            "committee",
            {
                "uid": ["committee"],
                "mail": ["committee@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def test_account_invitations_invalid_email_in_preview(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nnot-an-email,Alice Example,Hello\n",
            content_type="text/csv",
        )

        with patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()):
            resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid")

    def test_account_invitations_preview_marks_existing_users(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nexisting@example.com,Existing User,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.build_freeipa_email_lookup", return_value={}),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=["existinguser"]),
        ):
            resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Already exists")
        self.assertContains(resp, "existinguser")

    def test_account_invitations_preview_uses_bulk_freeipa_lookup(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nexisting@example.com,Existing User,Hello\n",
            content_type="text/csv",
        )

        freeipa_user = FreeIPAUser(
            "existinguser",
            {
                "uid": ["existinguser"],
                "mail": ["existing@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.account_invitations.FreeIPAUser.all", return_value=[freeipa_user]),
            patch(
                "core.account_invitations.FreeIPAUser.find_usernames_by_email",
                side_effect=AssertionError("per-email lookup should not run"),
            ),
        ):
            resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Already exists")
        self.assertContains(resp, "existinguser")

    def test_account_invitations_preview_uses_existing_accepted_invitation(self) -> None:
        self._login_as_freeipa_user("committee")

        AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            freeipa_matched_usernames=["accepteduser"],
        )

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\naccepted@example.com,Accepted User,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
        ):
            resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Already exists")
        self.assertContains(resp, "accepteduser")

    def test_account_invitations_upload_preview_and_send(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nalice@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        queued_email = SimpleNamespace(id=123)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email) as queue_mock,
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )

            self.assertEqual(preview_resp.status_code, 200)
            self.assertContains(preview_resp, "Invitation Preview")

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                    "email_template": "account-invite",
                },
            )

        self.assertEqual(send_resp.status_code, 302)
        invitation = AccountInvitation.objects.get(email="alice@example.com")
        self.assertEqual(invitation.send_count, 1)
        self.assertTrue(AccountInvitationSend.objects.filter(invitation=invitation).exists())
        _, kwargs = queue_mock.call_args
        self.assertNotIn("note", kwargs["context"])
        self.assertIn("invitation_token", kwargs["context"])
        self.assertEqual(kwargs["context"]["invitation_token"], str(invitation.invitation_token))
        self.assertIn("register_url", kwargs["context"])
        encoded_token = quote(str(invitation.invitation_token))
        self.assertIn(f"invite={encoded_token}", kwargs["context"]["register_url"])
        self.assertIn("login_url", kwargs["context"])
        self.assertIn(f"invite={encoded_token}", kwargs["context"]["login_url"])
        self.assertEqual(kwargs["context"].get("membership_committee_email"), settings.MEMBERSHIP_COMMITTEE_EMAIL)
        self.assertEqual(kwargs.get("reply_to"), [settings.MEMBERSHIP_COMMITTEE_EMAIL])

    @override_settings(ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES=["account-invite", "account-invite-alt"])
    def test_account_invitations_allows_alternate_template_and_stores(self) -> None:
        self._login_as_freeipa_user("committee")

        EmailTemplate.objects.update_or_create(
            name="account-invite-alt",
            defaults={
                "subject": "Alt invite",
                "content": "Hello {{ email }}",
                "html_content": "<p>Hello {{ email }}</p>",
            },
        )

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nalice@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        queued_email = SimpleNamespace(id=789)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email) as queue_mock,
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )
            self.assertEqual(preview_resp.status_code, 200)

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                    "email_template": "account-invite-alt",
                },
            )

        self.assertEqual(send_resp.status_code, 302)
        invitation = AccountInvitation.objects.get(email="alice@example.com")
        self.assertEqual(invitation.email_template_name, "account-invite-alt")
        self.assertTrue(
            AccountInvitationSend.objects.filter(invitation=invitation, template_name="account-invite-alt").exists()
        )
        _, kwargs = queue_mock.call_args
        self.assertEqual(kwargs["template_name"], "account-invite-alt")

    @override_settings(ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES=["account-invite", "account-invite-alt"])
    def test_account_invitation_resend_uses_stored_template(self) -> None:
        self._login_as_freeipa_user("committee")

        EmailTemplate.objects.update_or_create(
            name="account-invite-alt",
            defaults={
                "subject": "Alt invite",
                "content": "Hello {{ email }}",
                "html_content": "<p>Hello {{ email }}</p>",
            },
        )

        invitation = AccountInvitation.objects.create(
            email="bob@example.com",
            full_name="Bob Example",
            note="",
            invited_by_username="committee",
            email_template_name="account-invite-alt",
        )

        queued_email = SimpleNamespace(id=456)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email) as queue_mock,
        ):
            resend_resp = self.client.post(reverse("account-invitation-resend", args=[invitation.pk]))

        self.assertEqual(resend_resp.status_code, 302)
        _, kwargs = queue_mock.call_args
        self.assertEqual(kwargs["template_name"], "account-invite-alt")

    def test_account_invitations_send_skips_existing_users(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\naccepted@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=["alice"]),
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )
            self.assertEqual(preview_resp.status_code, 200)

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                },
            )

        self.assertEqual(send_resp.status_code, 302)
        self.assertFalse(AccountInvitation.objects.filter(email="accepted@example.com").exists())

    def test_account_invitations_get_refreshes_pending_acceptance(self) -> None:
        self._login_as_freeipa_user("committee")

        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            note="",
            invited_by_username="committee",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=["pendinguser"]),
        ):
            resp = self.client.get(reverse("account-invitations"))

        self.assertEqual(resp.status_code, 200)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(invitation.freeipa_matched_usernames, ["pendinguser"])

    def test_account_invitations_clears_stale_accepted(self) -> None:
        self._login_as_freeipa_user("committee")

        invitation = AccountInvitation.objects.create(
            email="stale@example.com",
            full_name="Stale User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            freeipa_matched_usernames=["staleuser"],
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.confirm_existing_usernames", return_value=([], True)),
        ):
            resp = self.client.get(reverse("account-invitations"))

        self.assertEqual(resp.status_code, 200)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertEqual(invitation.freeipa_matched_usernames, [])

    def test_account_invitation_resend_and_dismiss(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="bob@example.com",
            full_name="Bob Example",
            note="",
            invited_by_username="committee",
        )

        self._login_as_freeipa_user("committee")

        queued_email = SimpleNamespace(id=456)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email) as queue_mock,
        ):
            resend_resp = self.client.post(reverse("account-invitation-resend", args=[invitation.pk]))
            self.assertEqual(resend_resp.status_code, 302)

            dismiss_resp = self.client.post(reverse("account-invitation-dismiss", args=[invitation.pk]))
            self.assertEqual(dismiss_resp.status_code, 302)

        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.dismissed_at)
        self.assertEqual(invitation.send_count, 1)
        self.assertTrue(AccountInvitationSend.objects.filter(invitation=invitation).exists())
        _args, kwargs = queue_mock.call_args
        self.assertEqual(kwargs["context"].get("membership_committee_email"), settings.MEMBERSHIP_COMMITTEE_EMAIL)
        self.assertEqual(kwargs.get("reply_to"), [settings.MEMBERSHIP_COMMITTEE_EMAIL])

    def test_account_invitations_bulk_resend(self) -> None:
        self._login_as_freeipa_user("committee")

        first = AccountInvitation.objects.create(
            email="first@example.com",
            full_name="First Example",
            note="",
            invited_by_username="committee",
        )
        second = AccountInvitation.objects.create(
            email="second@example.com",
            full_name="Second Example",
            note="",
            invited_by_username="committee",
        )

        queued_email = SimpleNamespace(id=456)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email),
        ):
            resp = self.client.post(
                reverse("account-invitations-bulk"),
                data={
                    "bulk_action": "resend",
                    "selected": [str(first.pk), str(second.pk)],
                },
            )

        self.assertEqual(resp.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.send_count, 1)
        self.assertEqual(second.send_count, 1)
        self.assertEqual(AccountInvitationSend.objects.filter(invitation__in=[first, second]).count(), 2)

    def test_account_invitations_bulk_dismiss_accepted(self) -> None:
        self._login_as_freeipa_user("committee")

        invitation = AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
        )

        with patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()):
            resp = self.client.post(
                reverse("account-invitations-bulk"),
                data={
                    "bulk_action": "dismiss",
                    "bulk_scope": "accepted",
                    "selected": [str(invitation.pk)],
                },
            )

        self.assertEqual(resp.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.dismissed_at)
