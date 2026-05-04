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

    def test_settings_shell_template_exposes_current_vue_runtime_contract(self) -> None:
        template_content = self._read_template("settings_shell.html")

        for snippet in (
            'data-settings-root=""',
            'data-settings-api-url="{{ settings_api_url }}"',
            'data-settings-submit-url="{{ settings_submit_url }}"',
            'data-settings-csrf-token="{{ settings_csrf_token }}"',
            'settings_initial_payload|json_script:"settings-initial-payload"',
            'settings_route_config|json_script:"settings-route-config"',
            'Loading settings...',
            "src/entrypoints/settings.ts",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, template_content)

        self.assertNotIn("_settings_tabs.html", template_content)
        self.assertNotIn("_settings_tab_", template_content)
        self.assertNotIn("field=profile_form", template_content)
        self.assertNotIn("field=emails_form", template_content)
        self.assertNotIn("field=keys_form", template_content)
        self.assertNotIn("field=password_form", template_content)
