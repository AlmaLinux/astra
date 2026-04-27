
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse


class OTPSyncViewTests(TestCase):
    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_get_renders(self):
        client = Client()
        resp = client.get("/otp/sync/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-auth-recovery-otp-sync-root=""')
        self.assertContains(resp, f'data-auth-recovery-otp-sync-api-url="{reverse("api-otp-sync-detail")}"')
        self.assertContains(resp, 'data-auth-recovery-otp-sync-submit-url="/otp/sync/"')
        self.assertContains(resp, f'data-auth-recovery-otp-sync-login-url="{reverse("login")}"')
        self.assertNotContains(resp, 'id="id_username"')

    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_otp_sync_detail_api_returns_data_only_payload(self):
        response = self.client.get(reverse("api-otp-sync-detail"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["form"]["is_bound"])
        self.assertEqual(payload["form"]["fields"][0]["name"], "username")
        self.assertEqual(payload["form"]["fields"][-1]["name"], "token")
        self.assertNotIn("submit_url", payload)
        self.assertNotIn("login_url", payload)
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_post_success_redirects_to_login(self):
        django_client = Client()

        response = SimpleNamespace(ok=True, text="All good!")

        with patch("core.views_auth.requests.Session", autospec=True) as session_cls:
            session = session_cls.return_value
            session.post.return_value = response

            resp = django_client.post(
                "/otp/sync/",
                data={
                    "username": "alice",
                    "password": "pw",
                    "first_code": "123456",
                    "second_code": "234567",
                    "token": "",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/")

        # Messages are stored in the session of django_client
        follow = django_client.get(resp["Location"])
        msgs = [m.message for m in get_messages(follow.wsgi_request)]
        self.assertTrue(any("successfully" in m.lower() for m in msgs))

    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_post_rejected_shows_form_error(self):
        django_client = Client()

        response = SimpleNamespace(ok=True, text="Token sync rejected")

        with patch("core.views_auth.requests.Session", autospec=True) as session_cls:
            session = session_cls.return_value
            session.post.return_value = response

            resp = django_client.post(
                "/otp/sync/",
                data={
                    "username": "alice",
                    "password": "pw",
                    "first_code": "123456",
                    "second_code": "234567",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "not correct")

    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_post_no_server_shows_form_error(self):
        django_client = Client()

        with patch("core.views_auth.requests.Session", autospec=True) as session_cls:
            session = session_cls.return_value
            session.post.side_effect = requests.exceptions.RequestException("boom")

            resp = django_client.post(
                "/otp/sync/",
                data={
                    "username": "alice",
                    "password": "pw",
                    "first_code": "123456",
                    "second_code": "234567",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No IPA server available")

    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_post_unexpected_error_logs_structured_extra(self):
        django_client = Client()

        with (
            patch("core.views_auth.requests.Session", autospec=True) as session_cls,
            patch("core.views_auth.logger.exception", autospec=True) as mocked_log,
        ):
            session = session_cls.return_value
            session.post.side_effect = RuntimeError("boom")

            resp = django_client.post(
                "/otp/sync/",
                data={
                    "username": "alice",
                    "password": "pw",
                    "first_code": "123456",
                    "second_code": "234567",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Something went wrong")
        mocked_log.assert_called_once()

        log_kwargs = mocked_log.call_args.kwargs
        self.assertEqual(log_kwargs["extra"]["event"], "astra.auth.otp_sync.unexpected_error")
        self.assertEqual(log_kwargs["extra"]["component"], "auth")
        self.assertEqual(log_kwargs["extra"]["outcome"], "error")
        self.assertEqual(log_kwargs["extra"]["username"], "alice")
        self.assertEqual(log_kwargs["extra"]["error_type"], "RuntimeError")
        self.assertEqual(log_kwargs["extra"]["error_message"], "boom")
        self.assertIn("boom", log_kwargs["extra"]["error_repr"])
        self.assertEqual(log_kwargs["extra"]["error_args"], "('boom',)")

    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_post_rate_limit_denial_returns_429_and_skips_sync_request(self) -> None:
        django_client = Client()

        with (
            patch("core.views_auth.allow_request", return_value=False, create=True) as allow_mock,
            patch("core.views_auth.requests.Session", autospec=True) as session_cls,
            patch("core.views_auth.logger.warning", autospec=True) as warning_mock,
        ):
            response = django_client.post(
                reverse("otp-sync"),
                data={
                    "username": "alice",
                    "password": "pw",
                    "first_code": "123456",
                    "second_code": "234567",
                },
                REMOTE_ADDR="198.51.100.42",
                HTTP_X_FORWARDED_FOR="203.0.113.42, 198.51.100.42",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many OTP sync attempts", status_code=429)
        session_cls.assert_not_called()
        allow_mock.assert_called_once()
        warning_mock.assert_called_once()

        allow_kwargs = allow_mock.call_args.kwargs
        self.assertEqual(allow_kwargs["scope"], "auth.otp_sync")
        self.assertEqual(allow_kwargs["key_parts"], ["203.0.113.42", "alice"])

        log_extra = warning_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["event"], "astra.security.rate_limit.denied")
        self.assertEqual(log_extra["component"], "auth")
        self.assertEqual(log_extra["outcome"], "denied")
        self.assertEqual(log_extra["http_method"], "POST")
        self.assertIn("ip_hash", log_extra)
        self.assertIn("subject_hash", log_extra)
        self.assertNotIn("203.0.113.42", str(log_extra))
        self.assertNotIn("alice", str(log_extra).lower())
