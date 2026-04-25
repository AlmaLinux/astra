
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


class ProfileTimezoneDisplayTests(TestCase):
    def _add_session_and_messages(self, request: Any) -> Any:
        def get_response(_: Any) -> HttpResponse:
            return HttpResponse()

        SessionMiddleware(get_response).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_profile_prefers_freeipa_fasTimezone_for_display(self):
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
            patch("core.views_users._is_membership_committee_viewer", autospec=True, return_value=False),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["summary"]["timezoneName"], "Europe/Paris")
        self.assertTrue(payload["summary"]["currentTimeLabel"])
