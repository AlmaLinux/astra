
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase


class UnifiedSettingsTests(TestCase):
    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username, email=f"{username}@example.org")

    def test_settings_root_context_exposes_registry_driven_tabs(self):
        from core.settings_tabs import SETTINGS_TAB_REGISTRY
        from core.views_settings import settings_root

        factory = RequestFactory()
        request = factory.get("/settings/?tab=agreements")
        request.user = self._auth_user("alice")

        fake_user = SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="a@example.org",
            is_authenticated=True,
            groups_list=[],
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
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True),
            patch("core.views_settings.list_agreements_for_user", autospec=True, return_value=[]),
            patch("core.views_settings.render", autospec=True, side_effect=fake_render),
        ):
            response = settings_root(request)

        self.assertEqual(response.status_code, 200)
        context = captured["context"]
        self.assertEqual(context["tabs"], [tab.tab_id for tab in SETTINGS_TAB_REGISTRY])
        self.assertEqual(
            [tab.tab_id for tab in context["settings_tabs"]],
            [tab.tab_id for tab in SETTINGS_TAB_REGISTRY],
        )

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
        self.assertEqual(captured.get("template"), "core/settings_shell.html")
        ctx = captured.get("context")
        self.assertIsNotNone(ctx)
        tabs = captured["context"].get("tabs")
        self.assertTrue(tabs)
        self.assertIn("profile", tabs)
        self.assertIn("security", tabs)
        # OTP + Password are merged into Security.
        self.assertNotIn("otp", tabs)
        self.assertNotIn("password", tabs)
        self.assertIn("settings_initial_payload", captured["context"])
        self.assertIn("settings_route_config", captured["context"])

    def test_settings_root_context_sets_email_is_blacklisted_flag(self):
        from django_ses.models import BlacklistedEmail

        from core.views_settings import settings_root

        blacklisted_email = "a@example.org"
        BlacklistedEmail.objects.create(email=blacklisted_email)

        factory = RequestFactory()
        request = factory.get("/settings/?tab=emails")
        request.user = self._auth_user("alice")

        fake_user = SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="User",
            email=blacklisted_email,
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "mail": [blacklisted_email],
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
        self.assertTrue(captured["context"].get("email_is_blacklisted"))
