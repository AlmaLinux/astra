import datetime
import socket
from unittest.mock import patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_VIEW_MEMBERSHIP
from core.templatetags.core_membership_responses import membership_response_value


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


class MirrorBadgeEndpointTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        self.reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )
        self.member = FreeIPAUser(
            "member",
            {
                "uid": ["member"],
                "displayname": ["Member User"],
                "memberof_group": [],
            },
        )
        session = self.client.session
        session["_freeipa_username"] = "reviewer"
        session.save()

    def _request_badge(self, mirror_url: str):
        with patch("core.backends.FreeIPAUser.get", return_value=self.reviewer):
            return self.client.get(reverse("mirror-badge-svg"), {"url": mirror_url})

    def _request_badge_as(self, mirror_url: str, user: FreeIPAUser, username: str):
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()
        with patch("core.backends.FreeIPAUser.get", return_value=user):
            return self.client.get(reverse("mirror-badge-svg"), {"url": mirror_url})

    def test_fresh_mirror_returns_ok_badge(self) -> None:
        now_value = str(int(timezone.now().timestamp())).encode("utf-8")
        with patch("core.mirror_badge.requests.Session.get", return_value=_FakeResponse(200, now_value)):
            response = self._request_badge("https://1.1.1.1/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertContains(response, "ok", status_code=200)

    def test_stale_mirror_returns_stale_badge(self) -> None:
        stale_value = str(int((timezone.now() - datetime.timedelta(hours=48)).timestamp())).encode("utf-8")
        with patch("core.mirror_badge.requests.Session.get", return_value=_FakeResponse(200, stale_value)):
            response = self._request_badge("https://1.1.1.1/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "stale", status_code=200)

    def test_missing_time_returns_missing_badge(self) -> None:
        with patch("core.mirror_badge.requests.Session.get", return_value=_FakeResponse(404, b"")):
            response = self._request_badge("https://1.1.1.1/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "missing", status_code=200)

    def test_invalid_url_returns_invalid_badge(self) -> None:
        response = self._request_badge("not-a-url")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertContains(response, "invalid", status_code=200)

    def test_loopback_host_returns_blocked_badge_without_fetch(self) -> None:
        with patch("core.mirror_badge.requests.Session.get") as get_mock:
            response = self._request_badge("https://127.0.0.1/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertContains(response, "blocked", status_code=200)
        get_mock.assert_not_called()

    def test_dns_failure_returns_unreachable_badge(self) -> None:
        with (
            patch("core.mirror_badge.socket.getaddrinfo", side_effect=socket.gaierror("dns failure")),
            patch("core.mirror_badge.requests.Session.get") as get_mock,
        ):
            response = self._request_badge("https://repo.example.invalid/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertContains(response, "unreachable", status_code=200)
        get_mock.assert_not_called()

    def test_timeout_returns_unreachable_badge(self) -> None:
        with patch("core.mirror_badge.requests.Session.get", side_effect=requests.exceptions.Timeout("too slow")):
            response = self._request_badge("https://1.1.1.1/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "timed out", status_code=200)

    def test_unexpected_exception_returns_error_badge(self) -> None:
        with (
            patch("core.mirror_badge.socket.getaddrinfo") as getaddrinfo_mock,
            patch("core.mirror_badge.requests.Session.get", side_effect=TypeError("bad proxies")),
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
            response = self._request_badge("https://1.1.1.1/almalinux")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "error", status_code=200)

    def test_second_request_uses_cache_without_second_http_call(self) -> None:
        now_value = str(int(timezone.now().timestamp())).encode("utf-8")
        with (
            patch("core.mirror_badge.socket.getaddrinfo") as getaddrinfo_mock,
            patch("core.mirror_badge.requests.Session.get", return_value=_FakeResponse(200, now_value)) as get_mock,
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
            first = self._request_badge("https://1.1.1.1/almalinux")
            second = self._request_badge("https://1.1.1.1/almalinux")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(get_mock.call_count, 1)
        self.assertEqual(getaddrinfo_mock.call_count, 1)

    def test_second_blocked_request_uses_cached_status_without_second_dns_lookup(self) -> None:
        with (
            patch("core.mirror_badge.socket.getaddrinfo") as getaddrinfo_mock,
            patch("core.mirror_badge.requests.Session.get") as get_mock,
        ):
            getaddrinfo_mock.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", 443),
                )
            ]
            first = self._request_badge("https://repo.example.invalid/almalinux")
            second = self._request_badge("https://repo.example.invalid/almalinux")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertContains(first, "blocked", status_code=200)
        self.assertContains(second, "blocked", status_code=200)
        self.assertEqual(getaddrinfo_mock.call_count, 1)
        get_mock.assert_not_called()

    def test_logged_in_non_committee_user_can_access_badge(self) -> None:
        response = self._request_badge_as("not-a-url", self.member, "member")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid", status_code=200)

    def test_unauthenticated_user_is_redirected(self) -> None:
        session = self.client.session
        if "_freeipa_username" in session:
            del session["_freeipa_username"]
        session.save()

        response = self.client.get(reverse("mirror-badge-svg"), {"url": "https://1.1.1.1/almalinux"})

        self.assertEqual(response.status_code, 302)


class MembershipResponseValueMirrorBadgeTests(TestCase):
    def test_domain_response_appends_mirror_badge_img(self) -> None:
        rendered = membership_response_value("https://repo.example.com/almalinux", "Domain")

        self.assertIn('data-mirror-badge-container', rendered)
        self.assertIn('data-mirror-badge-img', rendered)
        self.assertIn(reverse("mirror-badge-svg"), rendered)
        self.assertIn('loading="eager"', rendered)

    def test_non_domain_url_response_does_not_append_mirror_badge(self) -> None:
        rendered = membership_response_value("https://github.com/example/repo/pull/1", "Pull request")

        self.assertNotIn('data-mirror-badge-container', rendered)
        self.assertNotIn('data-mirror-badge-img', rendered)
