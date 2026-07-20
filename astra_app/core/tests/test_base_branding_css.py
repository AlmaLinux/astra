from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class BaseBrandingCssTests(SimpleTestCase):
    def test_shared_shell_css_does_not_use_linear_gradients(self) -> None:
        css_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "css" / "base.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertNotIn("linear-gradient(", css)

    def test_shared_shell_css_uses_system_body_font_and_local_montserrat_for_shell_typography(self) -> None:
        css_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "css" / "base.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertNotIn("fonts.googleapis.com", css)
        self.assertNotIn("fonts.gstatic.com", css)
        self.assertIn('@font-face {\n  font-family: "Montserrat";', css)
        self.assertIn('src: url("../fonts/montserrat-400.ttf") format("truetype");', css)
        self.assertIn('src: url("../fonts/montserrat-600.ttf") format("truetype");', css)
        self.assertIn('src: url("../fonts/montserrat-700.ttf") format("truetype");', css)
        self.assertIn('src: url("../fonts/montserrat-800.ttf") format("truetype");', css)
        self.assertIn(
            'font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;',
            css,
        )
        self.assertIn(
            'h1,\nh2,\nh3,\nh4,\nh5,\nh6 {\n  font-family: Montserrat, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;',
            css,
        )
        self.assertNotIn(
            'body {\n  color: var(--alx-ink);\n  background: var(--al-c-soft-peach);\n  font-family: Montserrat, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;',
            css,
        )
        self.assertIn(
            '.main-header .nav-link,\n.main-header .navbar-nav .nav-link,\n.main-header .navbar-brand,\n.alx-brand-product {\n  font-family: Montserrat, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;',
            css,
        )

    def test_debug_and_email_preview_font_stacks_follow_style_book(self) -> None:
        css_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "css" / "base.css"
        js_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "js" / "templated_email.js"
        template_path = Path(settings.BASE_DIR) / "core" / "templates" / "core" / "debug_signals_log.html"

        css = css_path.read_text(encoding="utf-8")
        js = js_path.read_text(encoding="utf-8")
        template = template_path.read_text(encoding="utf-8")

        self.assertIn("font-family: var(--bs-font-monospace);", css)
        self.assertIn(
            'font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif',
            js,
        )
        self.assertNotIn("BlinkMacSystemFont", js)
        self.assertIn('style="font-family: var(--bs-font-monospace); font-size: 0.85em;"', template)

    def test_membership_standard_uses_brand_token_not_bootstrap_success_green(self) -> None:
        css_path = Path(settings.BASE_DIR) / "core" / "static" / "core" / "css" / "base.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            '.membership-standard {\n  color: var(--al-c-black-pearl-dark);\n  background-color: var(--al-c-atlantis);\n}',
            css,
        )
        self.assertNotIn("background-color: #28a745;", css)

    def test_user_visible_django_templates_do_not_use_long_dashes(self) -> None:
        template_root = Path(settings.BASE_DIR) / "core" / "templates" / "core"
        visible_templates = [
            "election_algorithm.html",
            "_membership_request_email_options.html",
            "_pagination.html",
            "organization_claim.html",
            "account_invitations_preview.html",
            "debug_signals_log.html",
        ]

        for relative_path in visible_templates:
            template_text = (template_root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("—", template_text, relative_path)
            self.assertNotIn("–", template_text, relative_path)