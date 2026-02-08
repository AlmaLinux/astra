
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings


class OTPSyncViewTests(TestCase):
    @override_settings(FREEIPA_HOST="ipa.test", FREEIPA_VERIFY_SSL=False)
    def test_get_renders(self):
        client = Client()
        resp = client.get("/otp/sync/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sync OTP Token")

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
