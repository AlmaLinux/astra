from pathlib import Path

from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase


class _SampleForm(forms.Form):
    name = forms.CharField(help_text="Original help")


class FormFieldDryPhase2TemplateTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_form_field_include_supports_phase2_options(self) -> None:
        invalid_form = _SampleForm(data={"name": ""})
        invalid_form.is_valid()

        rendered = Template(
            "{% include 'core/_form_field.html' with "
            "field=form.name wrapper_class='mb-0 col-md-6' "
            "help_text_override='Custom helper text' "
            "show_label=True show_errors=True show_help=True %}"
        ).render(Context({"form": invalid_form}))

        self.assertIn('class="form-group mb-0 col-md-6"', rendered)
        self.assertIn("<label", rendered)
        self.assertIn("text-danger", rendered)
        self.assertNotIn("Original help", rendered)
        self.assertIn("Custom helper text", rendered)

    def test_phase2_templates_use_extended_form_field_include(self) -> None:
        expectations: dict[str, list[str]] = {
            "_organization_contacts_tabs.html": [
                "{% include 'core/_form_field.html' with field=form.representative wrapper_class='mb-0' %}",
            ],
            "_settings_tab_security.html": [
                "{% include 'core/_form_field.html' with field=password_form.otp help_text_override='Your account has OTP enabled; enter your current OTP.' %}",
                "{% include 'core/_form_field.html' with field=otp_add_form.otp help_text_override='Enter your current OTP to authorize adding a new token.' %}",
            ],
            "email_template_edit.html": [
                "{% include 'core/_form_field.html' with field=form.name %}",
                "{% include 'core/_form_field.html' with field=form.description wrapper_class='mb-0' %}",
            ],
            "group_edit.html": [
                "{% include 'core/_form_field.html' with field=form.description show_errors=0 %}",
                "{% include 'core/_form_field.html' with field=form.fas_url show_errors=0 %}",
                "{% include 'core/_form_field.html' with field=form.fas_mailing_list show_errors=0 %}",
                "{% include 'core/_form_field.html' with field=form.fas_discussion_url show_errors=0 %}",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)
