from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse


class PasswordResetFlowTests(TestCase):
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
            patch("core.views_auth.ClientMeta", autospec=True, return_value=pw_client),
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


class AdminPasswordResetEmailTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_admin_send_password_reset_email(self):
        self._login_as_freeipa_admin("alice")

        from core.backends import FreeIPAUser

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})
        target_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "mail": ["bob@example.com"]})

        def _fake_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_get),
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

        from core.backends import FreeIPAUser

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
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.backends.FreeIPAUser.get_client", autospec=True, return_value=DummyClient()),
        ):
            resp = self.client.get(reverse("admin:auth_ipauser_change", args=["bob"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Reset user's password")
        self.assertContains(resp, reverse("admin:auth_ipauser_send_password_reset", args=["bob"]))
        self.assertContains(resp, "Disable user's OTP tokens")
        self.assertContains(resp, reverse("admin:auth_ipauser_disable_otp_tokens", args=["bob"]))

    def test_admin_change_form_hides_disable_otp_when_none(self):
        self._login_as_freeipa_admin("alice")

        from core.backends import FreeIPAUser

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
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.backends.FreeIPAUser.get_client", autospec=True, return_value=DummyClient()),
        ):
            resp = self.client.get(reverse("admin:auth_ipauser_change", args=["bob"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Reset user's password")
        self.assertNotContains(resp, "Disable user's OTP tokens")

    def test_admin_disable_otp_tokens(self):
        self._login_as_freeipa_admin("alice")

        from core.backends import FreeIPAUser

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
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.backends.FreeIPAUser.get_client", autospec=True, return_value=dummy),
        ):
            url = reverse("admin:auth_ipauser_disable_otp_tokens", args=["bob"])
            resp = self.client.post(url, data={"post": "1"}, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(dummy.disabled, ["token-1", "token-2"])
