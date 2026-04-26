from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class UserProfileVueTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_user_profile_page_renders_vue_controller_shell_contract(self) -> None:
        username = "alice"
        self._login_as_freeipa(username)

        with patch(
            "core.views_users._profile_context_for_user",
            side_effect=AssertionError("profile page route must not build server-rendered profile data"),
        ):
            response = self.client.get(f"/user/{username}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-user-profile-root")
        self.assertContains(response, f'data-user-profile-api-url="/api/v1/users/{username}/profile"')
        self.assertContains(response, 'data-user-profile-settings-profile-url="/settings/?tab=profile"')
        self.assertContains(response, 'data-user-profile-membership-history-url-template="/membership/log/__username__/?username=__username__"')
        self.assertContains(response, 'data-user-profile-membership-request-url="/membership/request/"')
        self.assertContains(response, 'data-user-profile-membership-request-detail-url-template="/membership/request/__request_id__/"')
        self.assertContains(response, 'data-user-profile-group-detail-url-template="/group/__group_name__/"')
        self.assertContains(response, 'data-user-profile-agreements-url-template="/settings/?tab=agreements&amp;agreement=__agreement_cn__"')
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/userProfile.ts"')
        self.assertNotContains(response, "data-user-profile-summary-root")
        self.assertNotContains(response, "user-profile-summary-bootstrap")
        self.assertNotContains(response, "data-user-profile-groups-root")
        self.assertNotContains(response, "user-profile-groups-bootstrap")

    def test_user_profile_api_returns_vue_profile_payload(self) -> None:
        username = "alice"
        self._login_as_freeipa(username)

        profile_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alice"],
                "sn": ["Example"],
                "mail": ["alice@example.test"],
                "timezone": ["UTC"],
                "fasIRCNick": ["aliceirc"],
            },
        )

        with (
            patch("core.views_users._get_full_user", return_value=profile_user),
            patch("core.views_users._is_membership_committee_viewer", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({username: "https://avatars.example/alice.png"}, 1, 0)),
        ):
            response = self.client.get(reverse("api-user-profile", args=[username]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["username"], username)
        self.assertEqual(payload["summary"]["fullName"], "Alice Example")
        self.assertEqual(payload["summary"]["email"], "alice@example.test")
        self.assertEqual(payload["summary"]["avatarUrl"], "https://avatars.example/alice.png")
        self.assertNotIn("profileEditUrl", payload["summary"])
        self.assertEqual(payload["groups"]["username"], username)
        self.assertIn("membership", payload)
        self.assertNotIn("historyUrl", payload["membership"])
        self.assertNotIn("requestUrl", payload["membership"])
        self.assertIn("accountSetup", payload)
