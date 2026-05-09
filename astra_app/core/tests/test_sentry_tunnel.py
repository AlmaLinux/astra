import json
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.views_sentry import sentry_browser_tunnel


@override_settings(SENTRY_DSN="https://public@example.ingest.sentry.io/1")
class SentryTunnelViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_tunnel_url_is_wired(self) -> None:
        response = self.client.get("/_ci/envelope/")

        self.assertEqual(response.status_code, 405)

    def test_tunnel_rejects_envelope_without_matching_dsn(self) -> None:
        response = self.client.post(
            "/_ci/envelope/",
            data=b'{"dsn":"https://public@other.ingest.sentry.io/2"}\n{}',
            content_type="application/x-sentry-envelope",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"ok": False, "error": "Invalid Sentry envelope DSN."})

    def test_tunnel_forwards_matching_envelope(self) -> None:
        upstream_response = Mock()
        upstream_response.status_code = 200
        upstream_response.content = b""
        upstream_response.headers = {"Content-Type": "text/plain"}

        envelope = b'{"dsn":"https://public@example.ingest.sentry.io/1"}\n{"type":"transaction"}\n{}'

        with patch("core.views_sentry.requests.post", return_value=upstream_response) as post_mock:
            response = self.client.post(
                "/_ci/envelope/",
                data=envelope,
                content_type="application/x-sentry-envelope",
            )

        self.assertEqual(response.status_code, 200)
        post_mock.assert_called_once_with(
            "https://example.ingest.sentry.io/api/1/envelope/",
            data=envelope,
            headers={"Content-Type": "application/x-sentry-envelope"},
            timeout=5,
            allow_redirects=False,
        )

    def test_tunnel_returns_bad_gateway_when_upstream_fails(self) -> None:
        envelope = b'{"dsn":"https://public@example.ingest.sentry.io/1"}\n{"type":"transaction"}\n{}'

        with patch(
            "core.views_sentry.requests.post",
            side_effect=requests.RequestException("downstream failed"),
        ):
            response = self.client.post(
                "/_ci/envelope/",
                data=envelope,
                content_type="application/x-sentry-envelope",
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"ok": False, "error": "Failed to forward Sentry envelope."})

    @override_settings(MAX_SENTRY_ENVELOPE_BYTES=32)
    def test_tunnel_rejects_oversized_envelope_before_forwarding(self) -> None:
        envelope = (
            b'{"dsn":"https://public@example.ingest.sentry.io/1"}\n'
            + b'{"type":"replay_event"}\n'
            + (b"x" * settings.MAX_SENTRY_ENVELOPE_BYTES)
        )

        with patch("core.views_sentry.requests.post") as post_mock:
            response = self.client.post(
                "/_ci/envelope/",
                data=envelope,
                content_type="application/x-sentry-envelope",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"ok": False, "error": "Sentry envelope too large."})
        post_mock.assert_not_called()

    @override_settings(MAX_SENTRY_ENVELOPE_BYTES=32)
    def test_tunnel_rejects_oversized_envelope_without_content_length(self) -> None:
        envelope = (
            b'{"dsn":"https://public@example.ingest.sentry.io/1"}\n'
            + b'{"type":"replay_event"}\n'
            + (b"x" * settings.MAX_SENTRY_ENVELOPE_BYTES)
        )
        request = self.factory.post(
            "/_ci/envelope/",
            data=envelope,
            content_type="application/x-sentry-envelope",
        )
        del request.META["CONTENT_LENGTH"]

        with patch("core.views_sentry.requests.post") as post_mock:
            response = sentry_browser_tunnel(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content),
            {"ok": False, "error": "Sentry envelope too large."},
        )
        post_mock.assert_not_called()

    @override_settings(MAX_SENTRY_ENVELOPE_BYTES=32)
    def test_tunnel_rejects_oversized_envelope_with_negative_content_length(self) -> None:
        envelope = (
            b'{"dsn":"https://public@example.ingest.sentry.io/1"}\n'
            + b'{"type":"replay_event"}\n'
            + (b"x" * settings.MAX_SENTRY_ENVELOPE_BYTES)
        )
        request = self.factory.post(
            "/_ci/envelope/",
            data=envelope,
            content_type="application/x-sentry-envelope",
        )
        request.META["CONTENT_LENGTH"] = "-1"

        with patch("core.views_sentry.requests.post") as post_mock:
            response = sentry_browser_tunnel(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.content),
            {"ok": False, "error": "Sentry envelope too large."},
        )
        post_mock.assert_not_called()