
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.account_invitations import find_account_invitation_matches
from core.backends import FreeIPAUser
from core.models import AccountInvitation, AccountInvitationSend, FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.views_account_invitations import _build_invitation_email_context


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

        EmailTemplate.objects.update_or_create(
            name="account-invite",
            defaults={
                "subject": "Account invite",
                "content": "Hello {{ email }}",
                "html_content": "<p>Hello {{ email }}</p>",
            },
        )
        EmailTemplate.objects.update_or_create(
            name="account-invite-org-claim",
            defaults={
                "subject": "Org claim invite",
                "content": "Hello {{ email }} {{ organization_name }} {{ claim_url }}",
                "html_content": "<p>Hello {{ email }} {{ organization_name }} {{ claim_url }}</p>",
            },
        )

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
            patch("core.views_account_invitations.build_freeipa_email_lookup", return_value={}),
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

    @override_settings(ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES=["account-invite", "account-invite-org-claim"])
    def test_account_invitations_upload_hides_org_claim_template_in_bulk_preview(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nalice@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={
                    "csv_file": upload,
                },
            )

        self.assertEqual(preview_resp.status_code, 200)
        self.assertContains(preview_resp, "account-invite")
        self.assertNotContains(preview_resp, settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)

    @override_settings(ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES=["account-invite", "account-invite-org-claim"])
    def test_account_invitations_send_rejects_org_claim_template_in_bulk_flow(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nalice@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=SimpleNamespace(id=101)) as queue_mock,
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={"csv_file": upload},
            )
            self.assertEqual(preview_resp.status_code, 200)

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                    "email_template": settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
                },
                follow=True,
            )

        self.assertEqual(send_resp.status_code, 200)
        self.assertContains(send_resp, "cannot be used for CSV bulk invitations")
        queue_mock.assert_not_called()
        self.assertFalse(AccountInvitation.objects.filter(email="alice@example.com").exists())

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

    @override_settings(
        ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES=[
            "account-invite",
            "account-invite-org-claim",
        ]
    )
    def test_account_invitation_resend_org_claim_template_includes_claim_context(self) -> None:
        self._login_as_freeipa_user("committee")

        organization = Organization.objects.create(
            name="Org Claim Target",
            business_contact_email="contact@example.com",
        )

        invitation = AccountInvitation.objects.create(
            email="contact@example.com",
            full_name="Contact Person",
            note="",
            invited_by_username="committee",
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            organization=organization,
        )

        queued_email = SimpleNamespace(id=999)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email) as queue_mock,
        ):
            resend_resp = self.client.post(reverse("account-invitation-resend", args=[invitation.pk]))

        self.assertEqual(resend_resp.status_code, 302)
        _args, kwargs = queue_mock.call_args
        self.assertEqual(kwargs["template_name"], settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)
        self.assertEqual(kwargs["context"]["organization_name"], "Org Claim Target")
        self.assertIn("claim_url", kwargs["context"])
        self.assertIn("/organizations/claim/", kwargs["context"]["claim_url"])

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

    def test_account_invitations_send_skips_org_linked_collision_without_mutation(self) -> None:
        self._login_as_freeipa_user("committee")

        organization = Organization.objects.create(
            name="Collision Org",
            business_contact_email="contact@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="contact@example.com",
            full_name="Original Name",
            note="Original note",
            invited_by_username="committee",
            organization=organization,
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
        )

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\ncontact@example.com,Changed Name,Changed note\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email") as queue_mock,
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={"csv_file": upload},
            )
            self.assertEqual(preview_resp.status_code, 200)

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                    "email_template": "account-invite",
                },
                follow=True,
            )

        self.assertEqual(send_resp.status_code, 200)
        self.assertContains(send_resp, "Skipped 1 organization-linked invitation row(s).")
        queue_mock.assert_not_called()

        invitation.refresh_from_db()
        self.assertEqual(invitation.full_name, "Original Name")
        self.assertEqual(invitation.note, "Original note")
        self.assertEqual(invitation.invited_by_username, "committee")
        self.assertEqual(invitation.email_template_name, settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)
        self.assertEqual(invitation.send_count, 0)

    @override_settings(PUBLIC_BASE_URL="")
    def test_account_invitations_send_surfaces_public_base_url_configuration_error(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nalice@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.views_account_invitations.queue_templated_email") as queue_mock,
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={"csv_file": upload},
            )
            self.assertEqual(preview_resp.status_code, 200)

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                    "email_template": "account-invite",
                },
                follow=True,
            )

        self.assertEqual(send_resp.status_code, 200)
        self.assertContains(send_resp, "PUBLIC_BASE_URL")
        self.assertContains(send_resp, "must be configured")
        queue_mock.assert_not_called()

    def test_account_invitations_send_treats_unexpected_value_error_as_send_failure(self) -> None:
        self._login_as_freeipa_user("committee")

        upload = SimpleUploadedFile(
            "invites.csv",
            b"email,full_name,note\nalice@example.com,Alice Example,Hello\n",
            content_type="text/csv",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
            patch(
                "core.views_account_invitations._build_invitation_email_context",
                side_effect=ValueError("unexpected value error"),
            ),
        ):
            preview_resp = self.client.post(
                reverse("account-invitations-upload"),
                data={"csv_file": upload},
            )
            self.assertEqual(preview_resp.status_code, 200)

            send_resp = self.client.post(
                reverse("account-invitations-send"),
                data={
                    "confirm": "1",
                    "email_template": "account-invite",
                },
                follow=True,
            )

        self.assertEqual(send_resp.status_code, 200)
        self.assertContains(send_resp, "Failed to queue 1 invitation(s).")
        self.assertNotContains(send_resp, "Invitation email configuration error")

        invitation = AccountInvitation.objects.get(email="alice@example.com")
        send = AccountInvitationSend.objects.get(invitation=invitation)
        self.assertEqual(send.result, AccountInvitationSend.Result.failed)
        self.assertEqual(send.error_category, "send_error")

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

    def test_account_invitations_get_does_not_accept_org_linked_invitation_from_account_match(self) -> None:
        self._login_as_freeipa_user("committee")

        organization = Organization.objects.create(
            name="Pending Claim Org",
            business_contact_email="contact@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="pending-claim@example.com",
            full_name="Pending Claim User",
            note="",
            invited_by_username="committee",
            organization=organization,
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=["pendingclaim"]),
        ):
            resp = self.client.get(reverse("account-invitations"))

        self.assertEqual(resp.status_code, 200)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertEqual(invitation.freeipa_matched_usernames, ["pendingclaim"])

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

    def test_account_invitations_list_shows_linked_organization_name_for_org_invites(self) -> None:
        self._login_as_freeipa_user("committee")

        organization = Organization.objects.create(
            name="Visibility Org",
            business_contact_email="contact@example.com",
        )
        AccountInvitation.objects.create(
            email="linked@example.com",
            full_name="Linked Person",
            note="",
            invited_by_username="committee",
            organization=organization,
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_account_invitations.find_account_invitation_matches", return_value=[]),
        ):
            response = self.client.get(reverse("account-invitations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility Org")
        self.assertContains(response, reverse("organization-detail", args=[organization.pk]))

    def test_account_invitations_list_shows_accepted_username_link_for_accepted_invitation(self) -> None:
        self._login_as_freeipa_user("committee")

        invitation = AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
            freeipa_matched_usernames=["accepteduser"],
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch(
                "core.views_account_invitations.confirm_existing_usernames",
                return_value=(invitation.freeipa_matched_usernames, True),
            ),
        ):
            response = self.client.get(reverse("account-invitations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accepted")
        accepted_user_url = reverse("user-profile", kwargs={"username": "accepteduser"})
        self.assertContains(
            response,
            f'<div class="text-muted small">as <a href="{accepted_user_url}">accepteduser</a></div>',
            html=True,
        )

    def test_account_invitations_list_shows_accepted_username_link_for_multiple_matches(self) -> None:
        self._login_as_freeipa_user("committee")

        invitation = AccountInvitation.objects.create(
            email="multi@example.com",
            full_name="Matched User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
            freeipa_matched_usernames=["alphauser", "accepteduser"],
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=self._committee_user()),
            patch(
                "core.views_account_invitations.confirm_existing_usernames",
                return_value=(invitation.freeipa_matched_usernames, True),
            ),
        ):
            response = self.client.get(reverse("account-invitations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accepted (multiple matches)")
        self.assertContains(response, "alphauser")
        self.assertContains(response, "accepteduser")
        self.assertContains(response, reverse("user-profile", kwargs={"username": "alphauser"}))
        accepted_user_url = reverse("user-profile", kwargs={"username": "accepteduser"})
        self.assertContains(
            response,
            f'<div class="text-muted small">as <a href="{accepted_user_url}">accepteduser</a></div>',
            html=True,
        )

    @override_settings(PUBLIC_BASE_URL="")
    def test_build_invitation_email_context_raises_when_public_base_url_missing(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="absolute@example.com",
            invited_by_username="committee",
        )

        with self.assertRaisesMessage(ValueError, "PUBLIC_BASE_URL"):
            _build_invitation_email_context(invitation=invitation, actor_username="committee")
