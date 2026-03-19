
import re
from html import unescape
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

        with patch("core.views_users._get_full_user", autospec=True, return_value=fake_user):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Ensure common attributes render when present.
        self.assertIn("en_US", content)
        self.assertIn("https://chat.almalinux.org/almalinux/messages/@alice_mm", content)
        self.assertIn("alice_irc", content)
        self.assertIn("alice_irc2", content)
        self.assertIn("bob:irc.example.org", content)
        self.assertIn("https://matrix.to/#/@alice:example.org", content)
        self.assertIn("https://example.com/blog", content)
        self.assertIn("https://example.com/rss", content)
        self.assertIn("alice@rhbz.example", content)
        self.assertIn("https://github.com/alicegh", content)
        self.assertIn("https://gitlab.com/alicegl", content)
        self.assertIn("0123456789ABCDEF", content)
        self.assertIn("ssh-ed25519", content)

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
                        f"{bsky_url}\n{bsky_subdomain_url}\n{mastodon_url}\n{x_url}\n{x_protocol_relative_url}\n{x_unsafe_scheme_url}\n"
                        f"{linkedin_url}\n{youtube_url}\n{instagram_url}\n{reddit_user_url}\n{reddit_subreddit_url}\n{tiktok_url}\n{signal_url}\n"
                        f"{normal_url}\n{schemeless_website_url}\n{unsafe_website_url}"
                    )
                ],
                "fasRssUrl": [schemeless_rss_url, unsafe_rss_url],
                "memberof_group": [],
            },
        )

        with patch("core.views_users._get_full_user", autospec=True, return_value=fake_user):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn(bsky_url, content)
        self.assertIn(bsky_subdomain_url, content)
        self.assertIn(mastodon_url, content)
        self.assertIn(x_url, content)
        self.assertIn(linkedin_url, content)
        self.assertIn(youtube_url, content)
        self.assertIn(instagram_url, content)
        self.assertIn(reddit_user_url, content)
        self.assertIn(reddit_subreddit_url, content)
        self.assertIn(tiktok_url, content)
        self.assertIn(signal_url, content)
        self.assertIn(normal_url, content)
        self.assertIn(schemeless_website_url, content)
        self.assertIn(unsafe_website_url, content)
        self.assertIn(schemeless_rss_url, content)
        self.assertIn(unsafe_rss_url, content)

        bsky_marker = 'title="Bluesky URLs"'
        mastodon_marker = 'title="Mastodon URLs"'
        x_marker = 'title="X (Twitter) URLs"'
        linkedin_marker = 'title="LinkedIn URLs"'
        youtube_marker = 'title="YouTube URLs"'
        instagram_marker = 'title="Instagram URLs"'
        reddit_marker = 'title="Reddit URLs"'
        tiktok_marker = 'title="TikTok URLs"'
        signal_marker = 'title="Signal URLs"'
        website_marker = 'title="Website URLs"'
        rss_marker = 'title="RSS URL"'

        self.assertIn(bsky_marker, content)
        self.assertIn(mastodon_marker, content)
        self.assertIn(x_marker, content)
        self.assertIn(linkedin_marker, content)
        self.assertIn(youtube_marker, content)
        self.assertIn(instagram_marker, content)
        self.assertIn(reddit_marker, content)
        self.assertIn(tiktok_marker, content)
        self.assertIn(signal_marker, content)
        self.assertIn(website_marker, content)
        self.assertIn(rss_marker, content)

        def _extract_list_item_by_marker(html: str, marker: str) -> str:
            idx = html.index(marker)
            li_start = html.rfind("<li ", 0, idx)
            li_end = html.find("</li>", idx)
            self.assertNotEqual(li_start, -1)
            self.assertNotEqual(li_end, -1)
            return html[li_start : li_end + len("</li>")]

        bsky_row = _extract_list_item_by_marker(content, bsky_marker)
        mastodon_row = _extract_list_item_by_marker(content, mastodon_marker)
        x_row = _extract_list_item_by_marker(content, x_marker)
        linkedin_row = _extract_list_item_by_marker(content, linkedin_marker)
        youtube_row = _extract_list_item_by_marker(content, youtube_marker)
        instagram_row = _extract_list_item_by_marker(content, instagram_marker)
        reddit_row = _extract_list_item_by_marker(content, reddit_marker)
        tiktok_row = _extract_list_item_by_marker(content, tiktok_marker)
        signal_row = _extract_list_item_by_marker(content, signal_marker)
        website_row = _extract_list_item_by_marker(content, website_marker)
        rss_row = _extract_list_item_by_marker(content, rss_marker)

        def _anchors_by_href(html: str) -> dict[str, list[str]]:
            # Keep this light and resilient: we only care about href + visible anchor text.
            # We intentionally avoid strict HTML parsing because the templates may introduce
            # harmless whitespace/newlines.
            anchor_re = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE)
            anchors: dict[str, list[str]] = {}
            for raw_href, raw_text in anchor_re.findall(html):
                href = unescape(raw_href)
                collapsed = re.sub(r"\s+", " ", unescape(raw_text)).strip()
                anchors.setdefault(href, []).append(collapsed)
            return anchors

        bsky_anchors = _anchors_by_href(bsky_row)
        mastodon_anchors = _anchors_by_href(mastodon_row)
        x_anchors = _anchors_by_href(x_row)
        linkedin_anchors = _anchors_by_href(linkedin_row)
        youtube_anchors = _anchors_by_href(youtube_row)
        instagram_anchors = _anchors_by_href(instagram_row)
        reddit_anchors = _anchors_by_href(reddit_row)
        tiktok_anchors = _anchors_by_href(tiktok_row)
        signal_anchors = _anchors_by_href(signal_row)
        website_anchors = _anchors_by_href(website_row)
        rss_anchors = _anchors_by_href(rss_row)

        bsky_normalized_href = f"https://{bsky_url}"
        bsky_subdomain_normalized_href = f"https://{bsky_subdomain_url}"
        x_protocol_relative_normalized_href = f"https:{x_protocol_relative_url}"
        schemeless_website_normalized_href = f"https://{schemeless_website_url}"
        schemeless_rss_normalized_href = f"https://{schemeless_rss_url}"

        # Social rows: href must keep the original URL, while the visible anchor text is a
        # derived handle/label (never the full raw URL).
        self.assertEqual(bsky_anchors[bsky_normalized_href], ["@alice.test"])
        self.assertNotEqual(bsky_anchors[bsky_normalized_href][0], bsky_url)

        # Bluesky subdomain URLs should display as @<host> when there's no /profile/<handle> segment.
        self.assertEqual(bsky_anchors[bsky_subdomain_normalized_href], [f"@{bsky_subdomain_url}"])

        self.assertEqual(mastodon_anchors[mastodon_url], ["@alice@mastodon.social"])
        self.assertNotEqual(mastodon_anchors[mastodon_url][0], mastodon_url)

        self.assertIn('fab fa-x-twitter', x_row)
        self.assertEqual(x_anchors[x_url], ["@alice"])
        self.assertNotEqual(x_anchors[x_url][0], x_url)

        self.assertEqual(x_anchors[x_protocol_relative_normalized_href], ["@bob"])
        # Unsafe schemes should not render as anchors.
        self.assertNotIn(x_unsafe_scheme_url, x_anchors)
        self.assertIn("@evil", x_row)

        # Best-effort platforms may parse a handle, otherwise they fall back to hostname.
        self.assertEqual(linkedin_anchors[linkedin_url], ["alice"])
        self.assertNotEqual(linkedin_anchors[linkedin_url][0], linkedin_url)

        self.assertEqual(youtube_anchors[youtube_url], ["youtu.be"])
        self.assertNotEqual(youtube_anchors[youtube_url][0], youtube_url)

        self.assertEqual(instagram_anchors[instagram_url], ["@alice"])
        self.assertNotEqual(instagram_anchors[instagram_url][0], instagram_url)

        self.assertEqual(reddit_anchors[reddit_user_url], ["u/alice"])
        self.assertNotEqual(reddit_anchors[reddit_user_url][0], reddit_user_url)
        self.assertEqual(reddit_anchors[reddit_subreddit_url], ["r/linux"])
        self.assertNotEqual(reddit_anchors[reddit_subreddit_url][0], reddit_subreddit_url)

        self.assertEqual(tiktok_anchors[tiktok_url], ["@alice"])
        self.assertNotEqual(tiktok_anchors[tiktok_url][0], tiktok_url)

        self.assertEqual(signal_anchors[signal_url], ["signal.me"])
        self.assertNotEqual(signal_anchors[signal_url][0], signal_url)

        # Website row: rendering is unchanged, so anchor text remains the full URL.
        self.assertEqual(website_anchors[normal_url], [normal_url])
        self.assertEqual(website_anchors[schemeless_website_normalized_href], [schemeless_website_url])
        self.assertNotIn(unsafe_website_url, website_anchors)
        self.assertIn(unsafe_website_url, website_row)

        self.assertEqual(rss_anchors[schemeless_rss_normalized_href], [schemeless_rss_url])
        self.assertNotIn(unsafe_rss_url, rss_anchors)
        self.assertIn(unsafe_rss_url, rss_row)

        self.assertNotIn(bsky_url, website_row)
        self.assertNotIn(mastodon_url, website_row)
        self.assertNotIn(x_url, website_row)
        self.assertNotIn(linkedin_url, website_row)
        self.assertNotIn(youtube_url, website_row)
        self.assertNotIn(instagram_url, website_row)
        self.assertNotIn(reddit_user_url, website_row)
        self.assertNotIn(reddit_subreddit_url, website_row)
        self.assertNotIn(tiktok_url, website_row)
        self.assertNotIn(signal_url, website_row)
        self.assertIn(normal_url, website_row)

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

        with patch("core.views_users._get_full_user", autospec=True, return_value=fake_user):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertNotIn("Timezone", content)
        self.assertNotIn("Current Time", content)
        self.assertNotIn('id="user-timezone"', content)
        self.assertNotIn('id="user-time"', content)

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

        with patch("core.views_users._get_full_user", autospec=True, return_value=fake_user):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertNotIn("Pronouns", content)
