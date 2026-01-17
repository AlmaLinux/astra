from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings

from core.backends import FreeIPAUser


class EmailChangeValidationFlowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username)

    def test_settings_email_validation_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="settings-email-validation").exists())

    @override_settings(SECRET_KEY="test-secret", EMAIL_VALIDATION_TOKEN_TTL_SECONDS=3600)
    def test_settings_emails_post_sends_validation_email_and_defers_update(self):
        from core import views_settings

        fu = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["old@example.org"],
                "fasRHBZEmail": [""],
                # Settings changes are gated by having a valid country.
                "fasstatusnote": ["US"],
            },
        )

        request = self.factory.post(
            "/settings/",
            data={"tab": "emails", "mail": "new@example.org", "fasRHBZEmail": ""},
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings._update_user_attrs", autospec=True) as update_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    update_mock.return_value = ([], True)
                    resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        update_mock.assert_not_called()
        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(send_mock.call_args.kwargs.get("template"), "settings-email-validation")

        ctx = send_mock.call_args.kwargs.get("context") or {}
        self.assertEqual(ctx.get("username"), "alice")
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)
        self.assertEqual(ctx.get("email_to_validate"), "new@example.org")
        self.assertIn("validate_url", ctx)

    @override_settings(SECRET_KEY="test-secret", EMAIL_VALIDATION_TOKEN_TTL_SECONDS=3600)
    def test_settings_emails_reuses_verified_mail_for_bugzilla_without_new_validation(self):
        from core import views_settings

        # User has a verified primary email already.
        fu = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["verified@example.org"],
                "fasRHBZEmail": [""],
                "fasstatusnote": ["US"],
            },
        )

        request = self.factory.post(
            "/settings/",
            data={"tab": "emails", "mail": "verified@example.org", "fasRHBZEmail": "verified@example.org"},
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings._update_user_attrs", autospec=True) as update_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    update_mock.return_value = ([], True)
                    resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        update_mock.assert_called_once()
        send_mock.assert_not_called()

    @override_settings(SECRET_KEY="test-secret", EMAIL_VALIDATION_TOKEN_TTL_SECONDS=3600)
    def test_settings_emails_reuses_verified_bugzilla_for_mail_without_new_validation(self):
        from core import views_settings

        # User has a verified Bugzilla email already.
        fu = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["old@example.org"],
                "fasRHBZEmail": ["verified-bz@example.org"],
                "fasstatusnote": ["US"],
            },
        )

        request = self.factory.post(
            "/settings/",
            data={
                "tab": "emails",
                "mail": "verified-bz@example.org",
                "fasRHBZEmail": "verified-bz@example.org",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings._update_user_attrs", autospec=True) as update_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    update_mock.return_value = ([], True)
                    resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        update_mock.assert_called_once()
        send_mock.assert_not_called()

    @override_settings(SECRET_KEY="test-secret", EMAIL_VALIDATION_TOKEN_TTL_SECONDS=3600)
    def test_settings_email_validate_get_and_post_applies_change(self):
        from core import views_settings
        from core.tokens import make_signed_token

        token = make_signed_token({"u": "alice", "a": "mail", "v": "new@example.org"})

        request_get = self.factory.get(f"/settings/emails/validate/?token={token}")
        self._add_session_and_messages(request_get)
        request_get.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            email="old@example.org",
            is_authenticated=True,
            _user_data={"mail": ["old@example.org"], "fasstatusnote": ["US"]},
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            resp_get = views_settings.settings_email_validate(request_get)

        self.assertEqual(resp_get.status_code, 200)
        html = resp_get.content.decode("utf-8")
        self.assertIn("new@example.org", html)

        request_post = self.factory.post(f"/settings/emails/validate/?token={token}")
        self._add_session_and_messages(request_post)
        request_post.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings._update_user_attrs", autospec=True) as update_mock:
                resp_post = views_settings.settings_email_validate(request_post)

        self.assertEqual(resp_post.status_code, 302)
        update_mock.assert_called_once()

    @override_settings(SECRET_KEY="test-secret", EMAIL_VALIDATION_TOKEN_TTL_SECONDS=3600)
    def test_settings_email_validate_rejects_wrong_user(self):
        from core import views_settings
        from core.tokens import make_signed_token

        token = make_signed_token({"u": "bob", "a": "mail", "v": "bob-new@example.org"})

        request = self.factory.get(f"/settings/emails/validate/?token={token}")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        resp = views_settings.settings_email_validate(request)
        self.assertEqual(resp.status_code, 302)
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("does not belong" in m.lower() for m in msgs))
