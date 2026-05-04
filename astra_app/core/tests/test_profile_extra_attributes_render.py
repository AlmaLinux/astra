
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core import views_users
from core.freeipa.user import FreeIPAUser


class ProfileExtraAttributesRenderTests(TestCase):
    def _add_session_and_messages(self, request: Any) -> Any:
        def get_response(_: Any) -> HttpResponse:
            return HttpResponse()

        SessionMiddleware(get_response).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_user_profile_detail_preserves_configured_extra_attributes(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["alice"]))

        setattr(
            request,
            "user",
            cast(
                Any,
                SimpleNamespace(
                    is_authenticated=True,
                    get_username=lambda: "alice",
                    username="alice",
                    email="a@example.org",
                    groups_list=[],
                ),
            ),
        )

        fake_user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["a@example.org"],
                "fasTimezone": ["Europe/Paris"],
                "fasLocale": ["en_US"],
                "fasIRCNick": [
                    "mattermost://alice_mm",
                    "alice_irc",
                    "matrix://example.org/alice",
                    "irc://irc.example.org/bob",
                    "alice_irc2",
                ],
                "fasWebsiteUrl": ["https://example.com/blog"],
                "fasRssUrl": ["https://example.com/rss"],
                "fasRHBZEmail": ["alice@rhbz.example"],
                "fasGitHubUsername": ["alicegh"],
                "fasGitLabUsername": ["alicegl"],
                "fasGPGKeyId": ["0123456789ABCDEF"],
                "ipasshpubkey": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE... alice@laptop"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fake_user),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            response = views_users.user_profile_detail_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        summary = payload["summary"]

        # Ensure common attributes render when present.
        self.assertEqual(summary["locale"], "en_US")
        self.assertEqual(
            summary["ircNicks"],
            [
                "mattermost://alice_mm",
                "alice_irc",
                "matrix://example.org/alice",
                "irc://irc.example.org/bob",
                "alice_irc2",
            ],
        )
        self.assertEqual(summary["websiteUrls"], ["https://example.com/blog"])
        self.assertEqual(summary["rssUrls"], ["https://example.com/rss"])
        self.assertEqual(summary["rhbzEmail"], "alice@rhbz.example")
        self.assertEqual(summary["githubUsername"], "alicegh")
        self.assertEqual(summary["gitlabUsername"], "alicegl")
        self.assertEqual(summary["gpgKeys"], ["0123456789ABCDEF"])
        self.assertEqual(summary["sshKeys"], ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE... alice@laptop"])

    def test_profile_detail_returns_raw_social_website_and_rss_data(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["alice"]))

        setattr(
            request,
            "user",
            cast(
                Any,
                SimpleNamespace(
                    is_authenticated=True,
                    get_username=lambda: "alice",
                    username="alice",
                    email="a@example.org",
                    groups_list=[],
                ),
            ),
        )

        bsky_url = "bsky.social./profile/alice.test"
        bsky_subdomain_url = "alice.bsky.social"
        x_url = "https://twitter.com/alice"
        x_protocol_relative_url = "//x.com/bob"
        x_unsafe_scheme_url = "javascript://twitter.com/evil"
        normal_url = "https://example.com"
        schemeless_website_url = "example.net/path"
        unsafe_website_url = "javascript:alert(1)"
        schemeless_rss_url = "feeds.example.com/rss"
        unsafe_rss_url = "ftp://example.com/rss"

        fake_user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["a@example.org"],
                "fasWebsiteUrl": [
                    (
                        f"{bsky_url}\n{bsky_subdomain_url}\n{x_url}\n{x_protocol_relative_url}\n{x_unsafe_scheme_url}\n"
                        f"{normal_url}\n{schemeless_website_url}\n{unsafe_website_url}"
                    )
                ],
                "fasRssUrl": [schemeless_rss_url, unsafe_rss_url],
                "memberof_group": [],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fake_user),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_users.membership_review_permissions", autospec=True, return_value={
                "membership_can_view": False,
                "membership_can_add": False,
                "membership_can_change": False,
                "membership_can_delete": False,
            }),
        ):
            response = views_users.user_profile_detail_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        summary = payload["summary"]

        self.assertEqual(
            summary["socialProfiles"],
            [
                {"platform": "bluesky", "urls": [bsky_url, bsky_subdomain_url]},
                {"platform": "x", "urls": [x_url, x_protocol_relative_url, x_unsafe_scheme_url]},
            ],
        )
        self.assertEqual(summary["websiteUrls"], [normal_url, schemeless_website_url, unsafe_website_url])
        self.assertEqual(summary["rssUrls"], [schemeless_rss_url, unsafe_rss_url])

    def test_user_profile_detail_hides_timezone_when_no_fasTimezone(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["alice"]))

        setattr(
            request,
            "user",
            cast(
                Any,
                SimpleNamespace(
                    is_authenticated=True,
                    get_username=lambda: "alice",
                    username="alice",
                    email="a@example.org",
                    groups_list=[],
                ),
            ),
        )

        fake_user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["a@example.org"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fake_user),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            response = views_users.user_profile_detail_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["summary"]["timezoneName"], "")
        self.assertNotIn("currentTimeLabel", payload["summary"])

    def test_user_profile_detail_hides_pronouns_when_no_pronouns_set(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["alice"]))

        setattr(
            request,
            "user",
            cast(
                Any,
                SimpleNamespace(
                    is_authenticated=True,
                    get_username=lambda: "alice",
                    username="alice",
                    email="a@example.org",
                    groups_list=[],
                ),
            ),
        )

        fake_user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["a@example.org"],
                "fasTimezone": ["Europe/Paris"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fake_user),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            response = views_users.user_profile_detail_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["summary"]["pronouns"], "")
