
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase


class UnifiedSettingsTests(TestCase):
    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username, email=f"{username}@example.org")

    def test_settings_root_renders_unified_settings_shell(self):
        from core.views_settings import settings_root

        factory = RequestFactory()
        request = factory.get("/settings/")
        request.user = self._auth_user("alice")

        fake_user = SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "fasstatusnote": ["US"],
            },
        )

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user),
            patch("core.views_settings.render", autospec=True, side_effect=fake_render),
        ):
            response = settings_root(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured.get("template"), "core/settings.html")
        ctx = captured.get("context")
        self.assertIsNotNone(ctx)
        tabs = captured["context"].get("tabs")
        self.assertTrue(tabs)
        self.assertIn("profile", tabs)
        self.assertIn("security", tabs)
        # OTP + Password are merged into Security.
        self.assertNotIn("otp", tabs)
        self.assertNotIn("password", tabs)
