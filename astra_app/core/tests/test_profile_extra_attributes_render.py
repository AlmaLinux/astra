
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

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

    def test_profile_renders_configured_extra_attributes(self) -> None:
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session_and_messages(request)

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
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

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
        self.assertEqual(summary["websiteUrls"], [{"href": "https://example.com/blog", "text": "https://example.com/blog"}])
        self.assertEqual(summary["rssUrls"], [{"href": "https://example.com/rss", "text": "https://example.com/rss"}])
        self.assertEqual(summary["rhbzEmail"], "alice@rhbz.example")
        self.assertEqual(summary["githubUsername"], "alicegh")
        self.assertEqual(summary["gitlabUsername"], "alicegl")
        self.assertEqual(summary["gpgKeys"], ["0123456789ABCDEF"])
        self.assertEqual(summary["sshKeys"], ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE... alice@laptop"])

    def test_profile_classifies_fasWebsiteUrl_social_domains(self) -> None:
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session_and_messages(request)

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
                ),
            ),
        )

        bsky_url = "bsky.social./profile/alice.test"
        bsky_subdomain_url = "alice.bsky.social"
        mastodon_url = "https://mastodon.social/@alice"
        mastodon_wildcard_url = "https://mastodon.example.org/@bob"
        x_url = "https://twitter.com/alice"
        x_protocol_relative_url = "//x.com/bob"
        x_unsafe_scheme_url = "javascript://twitter.com/evil"
        linkedin_url = "https://lnkd.in/in/alice"
        youtube_url = "https://youtu.be/dQw4w9WgXcQ"
        instagram_url = "https://instagr.am/alice"
        reddit_user_url = "https://www.reddit.com/u/alice"
        reddit_subreddit_url = "https://reddit.com/r/linux"
        tiktok_url = "https://www.tiktok.com/@alice"
        signal_url = "https://signal.me/#p/abcdef"
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
                # Some legacy FreeIPA data may store multiple URLs in a single value.
                "fasWebsiteUrl": [
                    (
                        f"{bsky_url}\n{bsky_subdomain_url}\n{mastodon_url}\n{mastodon_wildcard_url}\n{x_url}\n{x_protocol_relative_url}\n{x_unsafe_scheme_url}\n"
                        f"{linkedin_url}\n{youtube_url}\n{instagram_url}\n{reddit_user_url}\n{reddit_subreddit_url}\n{tiktok_url}\n{signal_url}\n"
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
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        summary = payload["summary"]
        social_profiles = {profile["label"]: profile for profile in summary["socialProfiles"]}

        bsky_normalized_href = f"https://{bsky_url}"
        bsky_subdomain_normalized_href = f"https://{bsky_subdomain_url}"
        x_protocol_relative_normalized_href = f"https:{x_protocol_relative_url}"
        schemeless_website_normalized_href = f"https://{schemeless_website_url}"
        schemeless_rss_normalized_href = f"https://{schemeless_rss_url}"

        self.assertEqual(
            social_profiles["Bluesky"]["urls"],
            [
                {"href": bsky_normalized_href, "text": "@alice.test"},
                {"href": bsky_subdomain_normalized_href, "text": f"@{bsky_subdomain_url}"},
            ],
        )
        self.assertEqual(
            social_profiles["Mastodon"]["urls"],
            [
                {"href": mastodon_url, "text": "@alice@mastodon.social"},
                {"href": mastodon_wildcard_url, "text": "@bob@mastodon.example.org"},
            ],
        )
        self.assertEqual(social_profiles["X (Twitter)"]["icon"], "fab fa-x-twitter")
        self.assertEqual(
            social_profiles["X (Twitter)"]["urls"],
            [
                {"href": x_url, "text": "@alice"},
                {"href": x_protocol_relative_normalized_href, "text": "@bob"},
                {"href": None, "text": "@evil"},
            ],
        )
        self.assertEqual(social_profiles["LinkedIn"]["urls"], [{"href": linkedin_url, "text": "alice"}])
        self.assertEqual(social_profiles["YouTube"]["urls"], [{"href": youtube_url, "text": "youtu.be"}])
        self.assertEqual(social_profiles["Instagram"]["urls"], [{"href": instagram_url, "text": "@alice"}])
        self.assertEqual(
            social_profiles["Reddit"]["urls"],
            [{"href": reddit_user_url, "text": "u/alice"}, {"href": reddit_subreddit_url, "text": "r/linux"}],
        )
        self.assertEqual(social_profiles["TikTok"]["urls"], [{"href": tiktok_url, "text": "@alice"}])
        self.assertEqual(social_profiles["Signal"]["urls"], [{"href": signal_url, "text": "signal.me"}])
        self.assertEqual(
            summary["websiteUrls"],
            [
                {"href": normal_url, "text": normal_url},
                {"href": schemeless_website_normalized_href, "text": schemeless_website_url},
                {"href": None, "text": unsafe_website_url},
            ],
        )
        self.assertEqual(
            summary["rssUrls"],
            [
                {"href": schemeless_rss_normalized_href, "text": schemeless_rss_url},
                {"href": None, "text": unsafe_rss_url},
            ],
        )

    def test_profile_hides_timezone_and_current_time_when_no_fasTimezone(self) -> None:
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session_and_messages(request)

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
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["summary"]["timezoneName"], "")
        self.assertEqual(payload["summary"]["currentTimeLabel"], "")

    def test_profile_hides_pronouns_row_when_no_pronouns_set(self) -> None:
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session_and_messages(request)

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
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["summary"]["pronouns"], "")
