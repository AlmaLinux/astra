import socket
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser


class _FakeResponse:
    def __init__(self, status_code: int, payload: bytes = b"") -> None:
        self.status_code = status_code
        self._payload = payload

    def iter_content(self, chunk_size: int = 1):
        if not self._payload:
            return iter(())
        start = 0
        while start < len(self._payload):
            stop = start + max(chunk_size, 1)
            yield self._payload[start:stop]
            start = stop

    def close(self) -> None:
        return None


class MirrorBadgeStatusEndpointTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.member = FreeIPAUser(
            "member",
            {
                "uid": ["member"],
                "displayname": ["Member User"],
                "memberof_group": [],
            },
        )
        session = self.client.session
        session["_freeipa_username"] = "member"
        session.save()

    def _request(self, mirror_url: str):
        with patch("core.backends.FreeIPAUser.get", return_value=self.member):
            return self.client.get(reverse("mirror-badge-status"), {"url": mirror_url})

    def test_invalid_url_returns_tooltip(self) -> None:
        response = self._request("not-a-url")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["key"], "invalid")
        self.assertIn("invalid", payload["tooltip"])

    def test_ok_status_returns_tooltip(self) -> None:
        now_value = str(int(timezone.now().timestamp())).encode("utf-8")
        with (
            patch("core.mirror_badge.socket.getaddrinfo") as getaddrinfo_mock,
            patch("core.mirror_badge.requests.Session.get", return_value=_FakeResponse(200, now_value)),
        ):
            getaddrinfo_mock.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("1.1.1.1", 443),
                )
            ]
            response = self._request("https://1.1.1.1/almalinux")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["key"], "ok")
        self.assertIn("ok", payload["tooltip"])

    def test_unauthenticated_user_is_redirected(self) -> None:
        session = self.client.session
        if "_freeipa_username" in session:
            del session["_freeipa_username"]
        session.save()

        response = self.client.get(reverse("mirror-badge-status"), {"url": "not-a-url"})
        self.assertEqual(response.status_code, 403)
