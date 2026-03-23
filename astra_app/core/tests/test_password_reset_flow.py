
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import unquote

from django.contrib.messages import get_messages
from django.contrib.staticfiles import finders
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from python_freeipa import exceptions

from core.models import AccountInvitation
from core.password_reset import normalize_last_password_change
from core.tokens import make_password_reset_token, read_password_reset_token
from core.views_auth import PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY


class PasswordResetFlowTests(TestCase):
    def test_normalize_last_password_change_noncanonical_dict_repr_returns_original(self) -> None:
        noncanonical_repr = "{'__datetime__': '20260323074752Z',}"
        self.assertEqual(normalize_last_password_change(noncanonical_repr), noncanonical_repr)

    def test_password_reset_email_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="password-reset").exists())

    def test_password_reset_success_email_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="password-reset-success").exists())

    @override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_password_reset_request_sends_email_for_existing_user(self):
        client = Client()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")
        self.assertEqual(queue_email_mock.call_count, 1)

        self.assertEqual(queue_email_mock.call_args.kwargs.get("template_name"), "password-reset")
        ctx = queue_email_mock.call_args.kwargs.get("context", {})
        self.assertEqual(ctx.get("username"), "alice")
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)
        reset_url = ctx.get("reset_url", "")
        self.assertTrue(reset_url.startswith("http://testserver/"))
        self.assertIn("/password-reset/confirm/?token=", reset_url)

        follow = client.get(resp["Location"])
        msgs = [m.message for m in get_messages(follow.wsgi_request)]
        self.assertTrue(any("email" in m.lower() and "password" in m.lower() for m in msgs))

    @override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_password_reset_email_includes_date_and_time_in_valid_until_utc(self) -> None:
        client = Client()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        context = queue_email_mock.call_args.kwargs["context"]
        self.assertRegex(context["valid_until_utc"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")

    def test_password_reset_request_does_not_send_for_unknown_user(self):
        client = Client()

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=None),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "does-not-exist"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")
        queue_email_mock.assert_not_called()

    def test_password_reset_request_logs_structured_extra_when_email_send_fails(self) -> None:
        client = Client()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.views_auth.find_user_for_password_reset", autospec=True, return_value=user),
            patch("core.views_auth.send_password_reset_email", autospec=True, side_effect=RuntimeError("boom")),
            patch("core.views_auth.logger.exception", autospec=True) as mocked_log,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")
        mocked_log.assert_called_once()

        log_kwargs = mocked_log.call_args.kwargs
        self.assertEqual(log_kwargs["extra"]["event"], "astra.auth.password_reset.email_send_failed")
        self.assertEqual(log_kwargs["extra"]["component"], "auth")
        self.assertEqual(log_kwargs["extra"]["outcome"], "error")
        self.assertEqual(log_kwargs["extra"]["username"], "alice")
        self.assertEqual(log_kwargs["extra"]["error_type"], "RuntimeError")
        self.assertEqual(log_kwargs["extra"]["error_message"], "boom")
        self.assertIn("boom", log_kwargs["extra"]["error_repr"])
        self.assertEqual(log_kwargs["extra"]["error_args"], "('boom',)")

    @override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_password_reset_request_embeds_pending_invitation_token_in_reset_token(self) -> None:
        client = Client()
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = str(invitation.invitation_token)
        session.save()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        reset_url = queue_email_mock.call_args.kwargs.get("context", {}).get("reset_url", "")
        token_match = re.search(r"token=([^\s&]+)", reset_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        token = unquote(token_match.group(1))
        payload = read_password_reset_token(token)
        self.assertEqual(payload.get("i"), str(invitation.invitation_token))
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)

    @override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_password_reset_request_omits_invalid_pending_invitation_token_from_reset_token(self) -> None:
        client = Client()

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = "invalid-token"
        session.save()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        reset_url = queue_email_mock.call_args.kwargs.get("context", {}).get("reset_url", "")
        token_match = re.search(r"token=([^\s&]+)", reset_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        token = unquote(token_match.group(1))
        payload = read_password_reset_token(token)
        self.assertNotIn("i", payload)

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_email_request_uses_canonical_lpc_source(self) -> None:
        client = Client()

        email_lookup_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        canonical_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="20260323090000Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        otp_lookup_client = SimpleNamespace(otptoken_find=lambda **_kwargs: {"result": []})

        with (
            patch("core.password_reset.FreeIPAUser.find_by_email", autospec=True, return_value=email_lookup_user),
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=canonical_user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=otp_lookup_client),
        ):
            request_resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice@example.com"},
                follow=False,
            )

            self.assertEqual(request_resp.status_code, 302)
            self.assertEqual(request_resp["Location"], "/login/")

            reset_url = queue_email_mock.call_args.kwargs.get("context", {}).get("reset_url", "")
            token_match = re.search(r"token=([^\s&]+)", reset_url)
            self.assertIsNotNone(token_match)
            assert token_match is not None
            token = unquote(token_match.group(1))

            confirm_resp = client.get(reverse("password-reset-confirm") + f"?token={token}", follow=False)

        self.assertEqual(confirm_resp.status_code, 200)
        self.assertContains(confirm_resp, "Set a new password")

    def test_password_reset_request_by_email_does_not_send_when_canonical_user_lookup_returns_none(self) -> None:
        client = Client()

        email_lookup_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.find_by_email", autospec=True, return_value=email_lookup_user),
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=None),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice@example.com"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")
        queue_email_mock.assert_not_called()

    def test_password_reset_request_by_email_does_not_send_when_canonical_user_lookup_raises(self) -> None:
        client = Client()

        email_lookup_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.find_by_email", autospec=True, return_value=email_lookup_user),
            patch("core.password_reset.FreeIPAUser.get", autospec=True, side_effect=RuntimeError("ipa unavailable")),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice@example.com"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")
        queue_email_mock.assert_not_called()

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_repeated_email_resets_do_not_false_fail_on_lpc_drift(self) -> None:
        client = Client()

        email_lookup_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        canonical_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="20260323090000Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        otp_lookup_client = SimpleNamespace(otptoken_find=lambda **_kwargs: {"result": []})

        with (
            patch("core.password_reset.FreeIPAUser.find_by_email", autospec=True, return_value=email_lookup_user),
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=canonical_user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=otp_lookup_client),
        ):
            for _ in range(2):
                request_resp = client.post(
                    reverse("password-reset"),
                    data={"username_or_email": "alice@example.com"},
                    follow=False,
                )
                self.assertEqual(request_resp.status_code, 302)
                self.assertEqual(request_resp["Location"], "/login/")

                reset_url = queue_email_mock.call_args.kwargs.get("context", {}).get("reset_url", "")
                token_match = re.search(r"token=([^\s&]+)", reset_url)
                self.assertIsNotNone(token_match)
                assert token_match is not None
                token = unquote(token_match.group(1))

                confirm_resp = client.get(reverse("password-reset-confirm") + f"?token={token}", follow=False)
                self.assertEqual(confirm_resp.status_code, 200)
                self.assertContains(confirm_resp, "Set a new password")

        self.assertEqual(queue_email_mock.call_count, 2)

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_lpc_mismatch_logs_warning_with_identifiers(self) -> None:
        client = Client()
        token = make_password_reset_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "lpc": "20260323090000Z",
            }
        )
        current_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="20260323100000Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.views_auth.find_user_for_password_reset", autospec=True, return_value=current_user),
            self.assertLogs("core.views_auth", level="WARNING") as log_capture,
        ):
            resp = client.get(reverse("password-reset-confirm") + f"?token={token}", follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/password-reset/")

        combined_logs = "\n".join(log_capture.output)
        self.assertIn("Password reset confirm rejected: token/user lpc mismatch", combined_logs)
        self.assertIn("username=alice", combined_logs)
        self.assertIn("token_email=alice@example.com", combined_logs)
        self.assertIn("user_email=alice@example.com", combined_logs)
        self.assertIn("token_lpc=20260323090000Z", combined_logs)
        self.assertIn("user_lpc=20260323100000Z", combined_logs)

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_accepts_dict_repr_lpc_when_user_lpc_matches(self) -> None:
        client = Client()
        token = make_password_reset_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "lpc": "{'__datetime__': '20260323074752Z'}",
            }
        )
        current_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="20260323074752Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        otp_lookup_client = SimpleNamespace(otptoken_find=lambda **_kwargs: {"result": []})

        with (
            patch("core.views_auth.find_user_for_password_reset", autospec=True, return_value=current_user),
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=otp_lookup_client),
        ):
            resp = client.get(reverse("password-reset-confirm") + f"?token={token}", follow=False)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Set a new password")

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_logs_info_for_token_parse_accept_and_completion(self) -> None:
        client = Client()
        token = make_password_reset_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "lpc": "20260323074752Z",
            }
        )
        current_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="20260323074752Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        svc_client = SimpleNamespace(
            user_mod=lambda *_args, **_kwargs: {"result": {"uid": ["alice"]}},
            otptoken_find=lambda **_kwargs: {"result": []},
        )
        pw_client = SimpleNamespace(change_password=lambda *_args, **_kwargs: True)

        with (
            patch("core.views_auth.find_user_for_password_reset", autospec=True, return_value=current_user),
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=svc_client),
            patch("core.views_auth._build_freeipa_client", autospec=True, return_value=pw_client),
            patch("core.views_auth.send_password_reset_success_email", autospec=True),
            self.assertLogs("core.views_auth", level="INFO") as log_capture,
        ):
            post_resp = client.post(
                reverse("password-reset-confirm"),
                data={
                    "token": token,
                    "password": "S3curePassword!",
                    "password_confirm": "S3curePassword!",
                    "otp": "",
                },
                follow=False,
            )

        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp["Location"], "/login/")

        combined_logs = "\n".join(log_capture.output)
        self.assertIn("Password reset confirm token parsed", combined_logs)
        self.assertIn("Password reset confirm accepted", combined_logs)
        self.assertIn("Password reset confirm completed", combined_logs)
        self.assertIn("username=alice", combined_logs)

    def test_password_reset_request_by_email_logs_warning_when_canonical_lookup_returns_none(self) -> None:
        client = Client()

        email_lookup_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.find_by_email", autospec=True, return_value=email_lookup_user),
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=None),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
            self.assertLogs("core.password_reset", level="WARNING") as log_capture,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice@example.com"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")
        queue_email_mock.assert_not_called()

        combined_logs = "\n".join(log_capture.output)
        self.assertIn("Password reset lookup canonicalization failed", combined_logs)
        self.assertIn("identifier_type=email", combined_logs)
        self.assertIn("identifier=alice@example.com", combined_logs)
        self.assertIn("resolved_username=alice", combined_logs)

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_rejects_when_token_lpc_differs_from_current_user_lpc(self) -> None:
        client = Client()
        token = make_password_reset_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "lpc": "20260323090000Z",
            }
        )
        current_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="20260323100000Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with patch("core.views_auth.find_user_for_password_reset", autospec=True, return_value=current_user):
            resp = client.get(reverse("password-reset-confirm") + f"?token={token}", follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/password-reset/")

        follow = client.get(resp["Location"])
        msgs = [message.message for message in get_messages(follow.wsgi_request)]
        self.assertIn(
            "Your password has changed since you requested this link. Please request a new password reset email.",
            msgs,
        )

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_sets_new_password(self):
        client = Client()

        # Arrange: request reset (generates token inside email context).
        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            resp = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        reset_url = queue_email_mock.call_args.kwargs.get("context", {}).get("reset_url", "")
        token_match = re.search(r"token=([^\s&]+)", reset_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        token = unquote(token_match.group(1))

        # GET renders the password form.
        with patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user):
            get_resp = client.get(reverse("password-reset-confirm") + f"?token={token}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Set a new password")

        svc_client = SimpleNamespace()
        svc_client.user_mod = lambda *_args, **_kwargs: {"result": {"uid": ["alice"]}}

        pw_client = SimpleNamespace()
        pw_client.change_password = lambda *_args, **_kwargs: True

        with (
            patch("core.password_reset.FreeIPAUser.get", autospec=True, return_value=user),
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=svc_client),
            patch("core.views_auth._build_freeipa_client", autospec=True, return_value=pw_client),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            post_resp = client.post(
                reverse("password-reset-confirm"),
                data={"token": token, "password": "S3curePassword!", "password_confirm": "S3curePassword!", "otp": ""},
                follow=False,
            )

        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp["Location"], "/login/")

        # Success email should be queued.
        self.assertGreaterEqual(queue_email_mock.call_count, 1)

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_reconciles_invitation_from_token_context(self) -> None:
        client = Client()
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        token = make_password_reset_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "lpc": "",
                "i": str(invitation.invitation_token),
            }
        )

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = str(invitation.invitation_token)
        session.save()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        svc_client = SimpleNamespace(
            user_mod=lambda *_args, **_kwargs: {"result": {"uid": ["alice"]}},
            otptoken_find=lambda **_kwargs: {"result": []},
        )
        pw_client = SimpleNamespace(change_password=lambda *_args, **_kwargs: True)

        with (
            patch("core.views_auth.find_user_for_password_reset", autospec=True, return_value=user),
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=svc_client),
            patch("core.views_auth._build_freeipa_client", autospec=True, return_value=pw_client),
            patch("core.password_reset.queue_templated_email", autospec=True),
        ):
            post_resp = client.post(
                reverse("password-reset-confirm"),
                data={"token": token, "password": "S3curePassword!", "password_confirm": "S3curePassword!", "otp": ""},
                follow=False,
            )

        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp["Location"], "/login/")
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(invitation.accepted_username, "alice")
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)

    @override_settings(PASSWORD_RESET_TOKEN_TTL_SECONDS=60 * 60)
    def test_password_reset_confirm_invalid_password_retry_preserves_invitation_context(self) -> None:
        client = Client()
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        token = make_password_reset_token(
            {
                "u": "alice",
                "e": "alice@example.com",
                "lpc": "",
                "i": str(invitation.invitation_token),
            }
        )

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = str(invitation.invitation_token)
        session.save()

        user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )
        refreshed_user = SimpleNamespace(
            username="alice",
            email="alice@example.com",
            last_password_change="2026-02-24T00:00:00Z",
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
        )

        svc_client = SimpleNamespace(
            user_mod=Mock(return_value={"result": {"uid": ["alice"]}}),
            otptoken_find=Mock(return_value={"result": [{"ipatokenuniqueid": ["token-1"]}]}),
        )
        pw_client = SimpleNamespace(
            change_password=Mock(side_effect=[exceptions.PWChangeInvalidPassword, True]),
        )

        with (
            patch(
                "core.views_auth.find_user_for_password_reset",
                autospec=True,
                side_effect=[user, refreshed_user, refreshed_user],
            ),
            patch("core.views_auth.FreeIPAUser.get_client", autospec=True, return_value=svc_client),
            patch("core.views_auth._build_freeipa_client", autospec=True, return_value=pw_client),
            patch("core.views_auth.send_password_reset_success_email", autospec=True),
        ):
            first_post = client.post(
                reverse("password-reset-confirm"),
                data={
                    "token": token,
                    "password": "S3curePassword!",
                    "password_confirm": "S3curePassword!",
                    "otp": "000000",
                },
                follow=False,
            )

            self.assertEqual(first_post.status_code, 200)
            retry_token = first_post.context["token"]
            self.assertNotEqual(retry_token, token)
            retry_payload = read_password_reset_token(retry_token)
            self.assertEqual(retry_payload.get("i"), str(invitation.invitation_token))
            self.assertEqual(retry_payload.get("lpc"), refreshed_user.last_password_change)

            second_post = client.post(
                reverse("password-reset-confirm"),
                data={
                    "token": retry_token,
                    "password": "S3curePassword!",
                    "password_confirm": "S3curePassword!",
                    "otp": "000000",
                },
                follow=False,
            )

        self.assertEqual(second_post.status_code, 302)
        self.assertEqual(second_post["Location"], "/login/")
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(invitation.accepted_username, "alice")


class AdminPasswordResetEmailTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_admin_send_password_reset_email(self):
        self._login_as_freeipa_admin("alice")

        from core.freeipa.user import FreeIPAUser

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})
        target_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "mail": ["bob@example.com"]})

        def _fake_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            url = reverse("admin:auth_ipauser_send_password_reset", args=["bob"])
            resp = self.client.post(url, data={"post": "1"}, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(queue_email_mock.call_count, 1)
        ctx = queue_email_mock.call_args.kwargs.get("context", {})
        self.assertEqual(ctx.get("username"), "bob")
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)
        self.assertTrue((ctx.get("reset_url") or "").startswith("http://testserver/"))

    def test_admin_change_form_shows_password_reset_and_disable_otp_tools(self):
        self._login_as_freeipa_admin("alice")

        from core.freeipa.user import FreeIPAUser

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})
        target_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "mail": ["bob@example.com"]})

        def _fake_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        class DummyClient:
            def user_find(self, **kwargs):
                return {"result": []}

            def otptoken_find(self, **kwargs):
                assert kwargs.get("o_ipatokenowner") == "bob"
                return {"result": [{"ipatokenuniqueid": ["token-1"]}]}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.freeipa.user.FreeIPAUser.get_client", autospec=True, return_value=DummyClient()),
        ):
            resp = self.client.get(reverse("admin:auth_ipauser_change", args=["bob"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Reset user's password")
        self.assertContains(resp, reverse("admin:auth_ipauser_send_password_reset", args=["bob"]))
        self.assertContains(resp, "Disable user's OTP tokens")
        self.assertContains(resp, reverse("admin:auth_ipauser_disable_otp_tokens", args=["bob"]))

    def test_admin_change_form_hides_disable_otp_when_none(self):
        self._login_as_freeipa_admin("alice")

        from core.freeipa.user import FreeIPAUser

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})
        target_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "mail": ["bob@example.com"]})

        def _fake_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        class DummyClient:
            def user_find(self, **kwargs):
                return {"result": []}

            def otptoken_find(self, **kwargs):
                assert kwargs.get("o_ipatokenowner") == "bob"
                return {"result": []}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.freeipa.user.FreeIPAUser.get_client", autospec=True, return_value=DummyClient()),
        ):
            resp = self.client.get(reverse("admin:auth_ipauser_change", args=["bob"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Reset user's password")
        self.assertNotContains(resp, "Disable user's OTP tokens")

    def test_admin_custom_css_overrides_jazzmin_modal_wrapper_blur(self) -> None:
        css_path = finders.find("core/css/admin.css")

        self.assertIsNotNone(css_path)
        css_content = Path(str(css_path)).read_text(encoding="utf-8")
        self.assertIn(".modal-open .wrapper", css_content)
        self.assertIn("filter: none !important;", css_content)

    def test_admin_disable_otp_tokens(self):
        self._login_as_freeipa_admin("alice")

        from core.freeipa.user import FreeIPAUser

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})
        target_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "mail": ["bob@example.com"]})

        def _fake_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        class DummyClient:
            def __init__(self):
                self.disabled: list[str] = []

            def user_find(self, **kwargs):
                return {"result": []}

            def otptoken_find(self, **kwargs):
                assert kwargs.get("o_ipatokenowner") == "bob"
                return {
                    "result": [
                        {"ipatokenuniqueid": ["token-1"], "ipatokendisabled": [False]},
                        {"ipatokenuniqueid": ["token-2"], "ipatokendisabled": [True]},
                    ]
                }

            def otptoken_mod(self, *, a_ipatokenuniqueid: str, o_ipatokendisabled: bool):
                assert o_ipatokendisabled is True
                self.disabled.append(a_ipatokenuniqueid)

        dummy = DummyClient()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.freeipa.user.FreeIPAUser.get_client", autospec=True, return_value=dummy),
        ):
            url = reverse("admin:auth_ipauser_disable_otp_tokens", args=["bob"])
            resp = self.client.post(url, data={"post": "1"}, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(dummy.disabled, ["token-1", "token-2"])
