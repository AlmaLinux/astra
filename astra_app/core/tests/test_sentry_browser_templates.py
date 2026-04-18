from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from core.context_processors import build_info
from core.freeipa.user import FreeIPAUser


class SentryBrowserContextTests(SimpleTestCase):
    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="build-123",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
    )
    def test_build_info_exposes_sentry_browser_loader_and_config(self) -> None:
        context = build_info(SimpleNamespace())

        self.assertEqual(
            context["sentry_browser_loader_src"],
            "https://js.sentry-cdn.com/public.min.js",
        )
        self.assertEqual(
            context["sentry_browser_config"],
            {
                "dsn": "https://public@example.ingest.sentry.io/1",
                "environment": "staging",
                "release": "build-123",
                "tracesSampleRate": 0.25,
            },
        )

    @override_settings(SENTRY_DSN="")
    def test_build_info_omits_sentry_browser_config_without_dsn(self) -> None:
        context = build_info(SimpleNamespace())

        self.assertEqual(context["sentry_browser_loader_src"], "")
        self.assertIsNone(context["sentry_browser_config"])


class SentryBrowserTemplateTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="build-123",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
    )
    def test_profile_page_includes_sentry_browser_loader_and_config(self) -> None:
        username = "admin"
        self._login_as_freeipa(username)

        freeipa_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["A"],
                "sn": ["Dmin"],
                "mail": ["admin@example.com"],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_user):
            response = self.client.get(f"/user/{username}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'src="https://js.sentry-cdn.com/public.min.js"',
        )
        self.assertContains(response, 'id="sentry-browser-config"')
        self.assertContains(response, "window.sentryOnLoad")
        self.assertContains(response, '"environment": "staging"')
        self.assertContains(response, '"release": "build-123"')
        self.assertContains(response, '"tracesSampleRate": 0.25')