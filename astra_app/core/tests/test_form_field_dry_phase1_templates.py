from pathlib import Path

from django.test import SimpleTestCase


class FormFieldDryPhase1TemplateTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_phase1_shell_templates_expose_current_vue_runtime_contract(self) -> None:
        expectations: dict[str, list[str]] = {
            "email_template_edit.html": [
                "data-email-template-editor-root=\"\"",
                "data-email-template-editor-api-url=\"{{ email_template_editor_api_url }}\"",
                'email-template-editor-initial-payload',
                'Loading template editor...',
                "src/entrypoints/emailTemplateEditor.ts",
            ],
            "password_reset_request.html": [
                "data-auth-recovery-password-reset-root=\"\"",
                "data-auth-recovery-password-reset-api-url=\"{{ auth_recovery_password_reset_api_url }}\"",
                "data-auth-recovery-initial-payload",
                "Loading password reset form...",
                "src/entrypoints/authRecovery.ts",
            ],
            "register_activate.html": [
                "data-register-activate-root=\"\"",
                "data-register-activate-api-url=\"{{ register_activate_api_url }}\"",
                "data-registration-initial-payload",
                "Loading activation form...",
                "src/entrypoints/registration.ts",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)

    def test_phase1_shell_templates_no_longer_inline_server_rendered_form_fields(self) -> None:
        expectations: dict[str, list[str]] = {
            "email_template_edit.html": ["field=form.name", "field=form.description"],
            "password_reset_request.html": ["field=form.username_or_email"],
            "register_activate.html": ["field=form.password", "field=form.password_confirm"],
        }

        for template_name, obsolete_snippets in expectations.items():
            template_content = self._read_template(template_name)
            for obsolete_snippet in obsolete_snippets:
                with self.subTest(template=template_name, obsolete_snippet=obsolete_snippet):
                    self.assertNotIn(obsolete_snippet, template_content)

    def test_server_rendered_settings_phase1_templates_keep_behavioral_contract(self) -> None:
        expectations: dict[str, list[str]] = {
            "_settings_tab_emails.html": [
                "Email delivery problem",
                "Please update your email address to a working one below.",
                "field=emails_form.mail",
                "field=emails_form.fasRHBZEmail",
            ],
            "_settings_tab_security.html": [
                "field=password_form.otp",
                "field=otp_add_form.otp",
                "Your account has OTP enabled; enter your current OTP.",
                "Enter your current OTP to authorize adding a new token.",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)
