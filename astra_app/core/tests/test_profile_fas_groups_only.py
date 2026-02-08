
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core import views_users
from core.backends import FreeIPAUser


class ProfileFasGroupsOnlyTests(TestCase):
    def _add_session_and_messages(self, request: Any) -> Any:
        def get_response(_: Any) -> HttpResponse:
            return HttpResponse()

        SessionMiddleware(get_response).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_profile_shows_only_fas_groups_and_links_to_group_page(self) -> None:
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
                "memberof_group": ["fas1", "ipa_only", "fas2"],
            },
        )

        groups = [
            SimpleNamespace(cn="fas1", fas_group=True, sponsors=[]),
            SimpleNamespace(cn="fas2", fas_group=True, sponsors=[]),
            SimpleNamespace(cn="ipa_only", fas_group=False, sponsors=[]),
        ]

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fake_user),
            patch("core.backends.FreeIPAGroup.all", return_value=groups),
        ):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn('href="/group/fas1/"', content)
        self.assertIn('href="/group/fas2/"', content)
        self.assertNotIn("ipa_only", content)
