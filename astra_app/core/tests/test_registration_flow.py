
import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings

from core.freeipa.user import FreeIPAUser
from core.models import AccountInvitation
from core.tests.utils_test_data import ensure_email_templates
from core.tokens import read_signed_token
from core.views_auth import PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY


class RegistrationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_email_templates()

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_invite_prefill_uses_invitation_loader(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        with patch(
            "core.views_registration.load_account_invitation_from_token",
            return_value=invitation,
        ) as load_mock:
            resp = client.get(f"/register/?invite={token}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["form"].initial.get("invitation_token"), token)
        load_mock.assert_called_once_with(token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_invite_stashes_pending_token_in_session(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        response = client.get(f"/register/?invite={token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY), token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_uses_session_pending_invite_when_query_absent(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        first_response = client.get(f"/register/?invite={token}")
        self.assertEqual(first_response.status_code, 200)

        second_response = client.get("/register/")

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.context["form"].initial.get("invitation_token"), token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_invalid_invite_token_is_not_stashed_or_used(self) -> None:
        client = Client()

        invalid_query_response = client.get("/register/?invite=invalid-token")

        self.assertEqual(invalid_query_response.status_code, 200)
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)
        self.assertEqual(invalid_query_response.context["form"].initial.get("invitation_token"), "")

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = "invalid-token"
        session.save()

        fallback_response = client.get("/register/")

        self.assertEqual(fallback_response.status_code, 200)
        self.assertEqual(fallback_response.context["form"].initial.get("invitation_token"), "")

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_confirm_resend_preserves_session_invitation_token_in_activation_payload(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        invitation_token = str(invitation.invitation_token)

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = invitation_token
        session.save()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            }
        }

        with (
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client),
            patch("core.views_registration.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            response = client.post(
                "/register/confirm/?username=alice",
                data={"username": "alice"},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)

        activate_url = queue_email_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        activation_payload = read_signed_token(unquote(token_match.group(1)))
        self.assertEqual(activation_payload.get("i"), invitation_token)

    def test_registration_email_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="registration-email-validation").exists())

    def test_account_invite_email_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="account-invite").exists())

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_renders(self):
        client = Client()
        resp = client.get("/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Create account")

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_creates_stage_user_and_sends_email(self):
        client = Client()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                post_office_send_mock.return_value = None

                resp = client.post(
                    "/register/",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "alice@example.com",
                        "over_16": "on",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/register/confirm"))
        # Registration email must use django-post-office's EmailTemplate feature
        self.assertEqual(post_office_send_mock.call_count, 1)
        self.assertEqual(post_office_send_mock.call_args.kwargs.get("template_name"), "registration-email-validation")

        ctx = post_office_send_mock.call_args.kwargs.get("context") or {}
        self.assertEqual(ctx.get("username"), "alice")
        self.assertEqual(ctx.get("first_name"), "Alice")
        self.assertEqual(ctx.get("last_name"), "User")
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_requires_over_16_checkbox(self):
        client = Client()

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True) as get_client_mock:
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                resp = client.post(
                    "/register/",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "alice@example.com",
                        # Missing: over_16
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "You must be over 16 years old to create an account")
        get_client_mock.assert_not_called()
        post_office_send_mock.assert_not_called()

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_links_invitation_by_token_on_activate(self):
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["different@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                resp = client.post(
                    f"/register/?invite={token}",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "different@example.com",
                        "over_16": "on",
                        "invitation_token": token,
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        activate_url = post_office_send_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        activation_token = token_match.group(1)

        ipa_client2 = SimpleNamespace()
        ipa_client2.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["different@example.com"],
            }
        }
        ipa_client2.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client2.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            with patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build:
                client_meta = mocked_build.return_value
                client_meta.change_password.return_value = None

                activation_post = client.post(
                    f"/register/activate/?token={activation_token}",
                    data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                    follow=False,
                )

        self.assertEqual(activation_post.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_activate_with_org_linked_invite_keeps_invitation_pending(self) -> None:
        from core.models import Organization

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

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["invitee@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                resp = client.post(
                    f"/register/?invite={token}",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "invitee@example.com",
                        "over_16": "on",
                        "invitation_token": token,
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        activate_url = post_office_send_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        activation_token = token_match.group(1)

        ipa_client2 = SimpleNamespace()
        ipa_client2.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["invitee@example.com"],
            }
        }
        ipa_client2.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client2.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            with patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build:
                client_meta = mocked_build.return_value
                client_meta.change_password.return_value = None

                activation_post = client.post(
                    f"/register/activate/?token={activation_token}",
                    data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                    follow=False,
                )

        self.assertEqual(activation_post.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)

    @override_settings(REGISTRATION_OPEN=True)
    def test_activate_invite_flow_uses_reconcile_helpers(self) -> None:
        from core.tokens import make_signed_token

        client = Client()

        invitation = AccountInvitation.objects.create(
            email="alice@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        activation_token = make_signed_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "i": str(invitation.invitation_token),
            }
        )

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = str(invitation.invitation_token)
        session.save()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            }
        }
        ipa_client.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with (
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client),
            patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build,
            patch(
                "core.views_registration.load_account_invitation_from_token",
                return_value=invitation,
            ) as load_mock,
            patch("core.views_registration.reconcile_account_invitation_for_username") as reconcile_mock,
        ):
            mocked_build.return_value.change_password.return_value = None
            activation_post = client.post(
                f"/register/activate/?token={activation_token}",
                data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                follow=False,
            )

        self.assertEqual(activation_post.status_code, 302)
        load_mock.assert_called_once_with(str(invitation.invitation_token))
        self.assertEqual(reconcile_mock.call_count, 1)
        self.assertEqual(reconcile_mock.call_args.kwargs["invitation"], invitation)
        self.assertEqual(reconcile_mock.call_args.kwargs["username"], "alice")
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_activate_flow_happy_path(self):
        client = Client()

        # Arrange: register to generate an email that contains the token.
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                post_office_send_mock.return_value = None

                resp = client.post(
                    "/register/",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "alice@example.com",
                        "over_16": "on",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        activate_url = post_office_send_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        token = token_match.group(1)

        # Activation GET renders a password form.
        ipa_client2 = SimpleNamespace()
        ipa_client2.stageuser_show = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }
        ipa_client2.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client2.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            activation_get = client.get(f"/register/activate/?token={token}")
        self.assertEqual(activation_get.status_code, 200)
        self.assertContains(activation_get, "Choose a password")

        # Activation POST activates stage user and sets password.

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            with patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build:
                client_meta = mocked_build.return_value
                client_meta.change_password.return_value = None

                activation_post = client.post(
                    f"/register/activate/?token={token}",
                    data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                    follow=False,
                )

        self.assertEqual(activation_post.status_code, 302)
        self.assertEqual(activation_post["Location"], "/login/")

        # Success message is stored in the session.
        follow = client.get(activation_post["Location"])
        msgs = [m.message for m in get_messages(follow.wsgi_request)]
        self.assertTrue(any("account" in m.lower() and "created" in m.lower() for m in msgs))

    def test_login_with_invite_token_links_invitation(self) -> None:
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
