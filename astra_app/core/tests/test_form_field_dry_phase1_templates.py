from pathlib import Path

from django.test import SimpleTestCase


class FormFieldDryPhase1TemplateTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_phase1_templates_use_form_field_include_for_target_fields(self) -> None:
        expectations: dict[str, list[str]] = {
            "email_template_edit.html": [
                "{% include 'core/_form_field.html' with field=form.name %}",
                "{% include 'core/_form_field.html' with field=form.description",
            ],
            "password_reset_request.html": [
                "{% include 'core/_form_field.html' with field=form.username_or_email %}",
            ],
            "register_activate.html": [
                "{% include 'core/_form_field.html' with field=form.password %}",
                "{% include 'core/_form_field.html' with field=form.password_confirm %}",
            ],
            "group_edit.html": [
                "{% include 'core/_form_field.html' with field=form.description",
                "{% include 'core/_form_field.html' with field=form.fas_url",
                "{% include 'core/_form_field.html' with field=form.fas_mailing_list",
                "{% include 'core/_form_field.html' with field=form.fas_discussion_url",
            ],
            "_settings_tab_emails.html": [
                "{% include 'core/_form_field.html' with field=emails_form.mail %}",
                "{% include 'core/_form_field.html' with field=emails_form.fasRHBZEmail %}",
            ],
            "_settings_tab_security.html": [
                "{% include 'core/_form_field.html' with field=password_form.otp",
                "{% include 'core/_form_field.html' with field=otp_add_form.otp",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)
