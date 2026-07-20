from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class BaseBrandingShellTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_public_shell_uses_icon_logo_and_account_services_label(self) -> None:
        response = self.client.get(reverse("privacy-policy"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/core/images/almalinux-logo.svg"')
        self.assertContains(response, "Account Services")
        self.assertContains(response, "Powered by AlmaLinux Astra")
        self.assertNotContains(response, 'class="brand-image img-circle"')
        self.assertContains(response, 'mask-icon" href="/static/core/images/fav/safari-pinned-tab.svg" color="#082336"')
        self.assertContains(response, "<h1", html=False)

    def test_authenticated_shell_uses_icon_logo_and_account_services_label(self) -> None:
        username = "admin"
        self._login_as_freeipa(username)

        freeipa_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["A"],
                "sn": ["Dmin"],
                "mail": ["admin@example.com"],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_user):
            response = self.client.get(f"/user/{username}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/core/images/almalinux-logo.svg"')
        self.assertContains(response, "Account Services")
        self.assertNotContains(response, 'class="brand-image img-circle"')
        self.assertContains(response, "Powered by AlmaLinux Astra")

    def test_shell_templates_load_base_css_that_enforces_montserrat_heading_and_nav_typography(self) -> None:
        public_response = self.client.get(reverse("privacy-policy"))

        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, 'href="/static/core/css/base.css"')

        css_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "css" / "base.css"
        css_text = css_path.read_text(encoding="utf-8")
        self.assertIn(
            'h1,\nh2,\nh3,\nh4,\nh5,\nh6 {\n  font-family: Montserrat, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;',
            css_text,
        )
        self.assertIn(
            '.main-header .nav-link,\n.main-header .navbar-nav .nav-link,\n.main-header .navbar-brand,\n.alx-brand-product {\n  font-family: Montserrat, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;',
            css_text,
        )