from unittest.mock import patch

from django.test import TestCase, override_settings


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

        with patch("core.avatar_providers.resolve_avatar_urls_for_users", return_value=({}, 0, 0)):
            response = self.client.get(f"/user/{username}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-user-profile-root")
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/userProfile.ts"')
        self.assertContains(response, "data-user-profile-summary-root")
        self.assertContains(response, "user-profile-summary-bootstrap")
        self.assertContains(response, "data-user-profile-groups-root")
        self.assertContains(response, "user-profile-groups-bootstrap")
