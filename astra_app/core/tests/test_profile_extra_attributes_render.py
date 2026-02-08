
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core import views_users
from core.backends import FreeIPAUser


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
                    "mattermost:/alice_mm",
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
        self.assertIn("mattermost://chat.almalinux.org/almalinux/messages/@alice_mm", content)
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
