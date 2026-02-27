from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from core.rate_limit import allow_request


@dataclass
class _Entry:
    value: int
    expires_at: float | None


class _RaceyCache:
    def __init__(self) -> None:
        self._now = 1000.0
        self._entries: dict[str, _Entry] = {}

    def _is_expired(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return True
        if entry.expires_at is None:
            return False
        return entry.expires_at <= self._now

    def add(self, key: str, value: int, timeout: int) -> bool:
        if key in self._entries and not self._is_expired(key):
            return False
        self._entries[key] = _Entry(value=value, expires_at=self._now + float(timeout))
        return True

    def incr(self, key: str) -> int:
        if self._is_expired(key):
            raise ValueError("Key does not exist")
        entry = self._entries[key]
        entry.value += 1
        entry.expires_at = None
        return entry.value

    def touch(self, key: str, timeout: int) -> bool:
        if self._is_expired(key):
            return False
        self._entries[key].expires_at = self._now + float(timeout)
        return True

    def set(self, key: str, value: int, timeout: int) -> None:
        self._entries[key] = _Entry(value=value, expires_at=self._now + float(timeout))

    def get_expiry(self, key: str) -> float | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        return entry.expires_at

    def advance(self, seconds: int) -> None:
        self._now += float(seconds)


class RateLimitTtlBehaviorTests(TestCase):
    def test_allow_request_reapplies_ttl_after_increment_when_backend_drops_expiry(self) -> None:
        cache_backend = _RaceyCache()
        with patch("core.rate_limit.cache", cache_backend):
            first_allowed = allow_request(
                scope="auth.login",
                key_parts=["198.51.100.10", "alice"],
                limit=3,
                window_seconds=60,
            )
            second_allowed = allow_request(
                scope="auth.login",
                key_parts=["198.51.100.10", "alice"],
                limit=3,
                window_seconds=60,
            )

        self.assertTrue(first_allowed)
        self.assertTrue(second_allowed)

        cache_key = next(iter(cache_backend._entries.keys()))
        self.assertIsNotNone(cache_backend.get_expiry(cache_key))

    def test_allow_request_allows_again_after_window_expires(self) -> None:
        cache_backend = _RaceyCache()
        with patch("core.rate_limit.cache", cache_backend):
            self.assertTrue(
                allow_request(
                    scope="auth.password_reset",
                    key_parts=["198.51.100.11", "alice@example.com"],
                    limit=1,
                    window_seconds=5,
                )
            )
            self.assertFalse(
                allow_request(
                    scope="auth.password_reset",
                    key_parts=["198.51.100.11", "alice@example.com"],
                    limit=1,
                    window_seconds=5,
                )
            )
            cache_backend.advance(6)
            self.assertTrue(
                allow_request(
                    scope="auth.password_reset",
                    key_parts=["198.51.100.11", "alice@example.com"],
                    limit=1,
                    window_seconds=5,
                )
            )


class AuthRateLimitEndpointTests(TestCase):
    def test_login_rate_limit_key_uses_remote_addr_when_x_forwarded_for_present(self) -> None:
        client = Client()

        with (
            patch("core.views_auth.allow_request", return_value=False, create=True) as allow_mock,
            patch("core.views_auth.logger.warning"),
            patch("django.contrib.auth.forms.authenticate", autospec=True, return_value=None),
        ):
            response = client.post(
                reverse("login"),
                data={"username": "Alice", "password": "bad-password"},
                REMOTE_ADDR="198.51.100.99",
                HTTP_X_FORWARDED_FOR="203.0.113.7, 198.51.100.99",
            )

        self.assertEqual(response.status_code, 429)
        allow_kwargs = allow_mock.call_args.kwargs
        self.assertEqual(allow_kwargs["scope"], "auth.login")
        self.assertEqual(allow_kwargs["key_parts"][0], "198.51.100.99")

    def test_login_denial_returns_error_and_emits_structured_log(self) -> None:
        client = Client()

        with (
            patch("core.views_auth.allow_request", return_value=False, create=True) as allow_mock,
            patch("core.views_auth.logger.warning") as warning_mock,
            patch("django.contrib.auth.forms.authenticate", autospec=True, return_value=None),
        ):
            response = client.post(
                reverse("login"),
                data={"username": "Alice", "password": "bad-password"},
                REMOTE_ADDR="198.51.100.20",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many login attempts", status_code=429)
        allow_mock.assert_called_once()
        warning_mock.assert_called_once()

        allow_kwargs = allow_mock.call_args.kwargs
        self.assertEqual(allow_kwargs["scope"], "auth.login")
        self.assertEqual(allow_kwargs["key_parts"], ["198.51.100.20", "alice"])

        log_extra = warning_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["event"], "astra.security.rate_limit.denied")
        self.assertEqual(log_extra["component"], "auth")
        self.assertEqual(log_extra["outcome"], "denied")
        self.assertEqual(log_extra["http_method"], "POST")
        self.assertIn("ip_hash", log_extra)
        self.assertIn("subject_hash", log_extra)
        self.assertNotIn("198.51.100.20", str(log_extra))
        self.assertNotIn("alice", str(log_extra).lower())

    def test_password_reset_denial_returns_error_and_does_not_send_email(self) -> None:
        client = Client()

        with (
            patch("core.views_auth.allow_request", return_value=False, create=True) as allow_mock,
            patch("core.views_auth.logger.warning") as warning_mock,
            patch("core.password_reset.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            response = client.post(
                reverse("password-reset"),
                data={"username_or_email": "alice@example.com"},
                REMOTE_ADDR="198.51.100.21",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many password reset attempts", status_code=429)
        queue_email_mock.assert_not_called()
        allow_mock.assert_called_once()
        warning_mock.assert_called_once()

        allow_kwargs = allow_mock.call_args.kwargs
        self.assertEqual(allow_kwargs["scope"], "auth.password_reset_request")
        self.assertEqual(allow_kwargs["key_parts"], ["198.51.100.21", "alice@example.com"])

        log_extra = warning_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["event"], "astra.security.rate_limit.denied")
        self.assertEqual(log_extra["component"], "auth")
        self.assertEqual(log_extra["outcome"], "denied")
        self.assertEqual(log_extra["http_method"], "POST")
        self.assertIn("ip_hash", log_extra)
        self.assertIn("subject_hash", log_extra)
        self.assertNotIn("198.51.100.21", str(log_extra))
        self.assertNotIn("alice@example.com", str(log_extra).lower())

    def test_registration_denial_returns_error_and_does_not_stage_user(self) -> None:
        client = Client()

        with (
            patch("core.views_registration.allow_request", return_value=False, create=True) as allow_mock,
            patch("core.views_auth.logger.warning") as warning_mock,
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True) as get_client_mock,
        ):
            response = client.post(
                reverse("register"),
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                },
                REMOTE_ADDR="198.51.100.22",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many registration attempts", status_code=429)
        get_client_mock.assert_not_called()
        allow_mock.assert_called_once()
        warning_mock.assert_called_once()

        allow_kwargs = allow_mock.call_args.kwargs
        self.assertEqual(allow_kwargs["scope"], "auth.registration_initiation")
        self.assertEqual(allow_kwargs["key_parts"], ["198.51.100.22", "alice", "alice@example.com"])

        log_extra = warning_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["event"], "astra.security.rate_limit.denied")
        self.assertEqual(log_extra["component"], "auth")
        self.assertEqual(log_extra["outcome"], "denied")
        self.assertEqual(log_extra["http_method"], "POST")
        self.assertIn("ip_hash", log_extra)
        self.assertIn("subject_hash", log_extra)
        self.assertNotIn("198.51.100.22", str(log_extra))
        self.assertNotIn("alice@example.com", str(log_extra).lower())