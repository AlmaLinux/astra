from pathlib import Path

from django.test import SimpleTestCase


class FormFieldDryPhase4TemplateTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_phase4_templates_use_shared_form_field_includes(self) -> None:
        expectations: dict[str, list[str]] = {
            "election_edit.html": [
                "{% include 'core/_form_field.html' with field=details_form.name %}",
                "{% include 'core/_form_field.html' with field=details_form.start_datetime wrapper_class='col-md-6' %}",
                "{% include 'core/_form_field_inner.html' with field=f.freeipa_username show_label=0 show_help=0 %}",
                "{% include 'core/_form_field_inner.html' with field=g.candidate_usernames show_label=0 show_help=0 %}",
            ],
            "_templated_email_compose.html": [
                "{% include 'core/_form_field.html' with field=form.subject %}",
                "{% include 'core/_form_field_inner.html' with field=form.html_content show_label=0 show_help=0 %}",
                "{% include 'core/_form_field_inner.html' with field=form.text_content show_label=0 show_help=0 %}",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)

        send_mail_shell = self._read_template("send_mail_shell.html")
        self.assertIn("_templated_email_compose_assets_head.html", send_mail_shell)
        self.assertIn("_templated_email_compose_assets_scripts.html", send_mail_shell)
        self.assertIn("data-send-mail-root", send_mail_shell)
        self.assertNotIn("data-templated-email-compose", send_mail_shell)

    def test_phase4_settings_shell_excludes_legacy_settings_partials(self) -> None:
        settings_shell = self._read_template("settings_shell.html")

        self.assertNotIn("_settings_tabs.html", settings_shell)
        self.assertNotIn("_settings_tab_profile.html", settings_shell)
        self.assertNotIn("_settings_tab_keys.html", settings_shell)
        self.assertNotIn("_modal_avatar_settings.html", settings_shell)
        self.assertNotIn("core/_form_field.html", settings_shell)
        self.assertNotIn("core/_form_field_widget_fallback.html", settings_shell)

