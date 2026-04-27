from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class SettingsShellApiTests(TestCase):
    def _login_as_freeipa_user(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _fake_user(self) -> SimpleNamespace:
        return SimpleNamespace(
            username="alice",
            email="alice@example.org",
            is_authenticated=True,
            groups_list=[],
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "mail": ["alice@example.org"],
                "fasstatusnote": ["US"],
            },
        )

    def test_settings_page_bootstrap_uses_canonical_detail_endpoint(self) -> None:
        self._login_as_freeipa_user()
        otp_client = SimpleNamespace(otptoken_find=lambda **_kwargs: {"result": []})

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_user()),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_settings._get_freeipa_client", autospec=True, return_value=otp_client),
        ):
            page_response = self.client.get(f'{reverse("settings")}?tab=security')
            api_response = self.client.get(f'{reverse("api-settings-detail")}?tab=security')

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'data-settings-root=""')
        self.assertContains(
            page_response,
            f'data-settings-api-url="{reverse("api-settings-detail")}?tab=security"',
        )
        self.assertContains(page_response, f'data-settings-submit-url="{reverse("settings")}"')
        self.assertNotContains(page_response, 'id="id_current_password"')

        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertEqual(payload["active_tab"], "security")
        self.assertEqual(payload["tabs"], ["profile", "emails", "keys", "security", "privacy", "membership"])
        self.assertIn("password", payload["security"])
        self.assertNotIn("submit_url", payload)
        self.assertNotIn("settings_url", payload)
        self.assertNotIn("tab_labels", payload)
        self.assertEqual(api_response["Cache-Control"], "private, no-cache")

    def test_settings_detail_api_rejects_non_get(self) -> None:
        self._login_as_freeipa_user()

        response = self.client.post(reverse("api-settings-detail"))

        self.assertEqual(response.status_code, 405)
        self.assertJSONEqual(response.content, {"error": "Method not allowed."})
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_settings_detail_api_builds_json_without_rendering_shell(self) -> None:
        self._login_as_freeipa_user()
        otp_client = SimpleNamespace(otptoken_find=lambda **_kwargs: {"result": []})

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_user()),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_settings._get_freeipa_client", autospec=True, return_value=otp_client),
            patch("core.views_settings.render", autospec=True, side_effect=AssertionError("render should not be called")),
        ):
            response = self.client.get(f'{reverse("api-settings-detail")}?tab=security')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["active_tab"], "security")
        self.assertIn("security", payload)
        self.assertEqual(response["Cache-Control"], "private, no-cache")