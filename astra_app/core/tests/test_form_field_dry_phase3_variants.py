from pathlib import Path

from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase


class _Phase3Form(forms.Form):
    over_16 = forms.BooleanField(required=True, label="I am over 16")
    country_code = forms.CharField(help_text="Country help")
    github = forms.CharField()


class FormFieldDryPhase3VariantTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_phase3_variant_includes_render_expected_patterns(self) -> None:
        invalid_form = _Phase3Form(data={"over_16": "", "country_code": "", "github": ""})
        invalid_form.is_valid()

        checkbox_html = Template(
            "{% include 'core/_form_field_checkbox.html' with field=form.over_16 %}"
        ).render(Context({"form": invalid_form}))
        self.assertIn('class="form-group form-check"', checkbox_html)
        self.assertIn('class="form-check-label"', checkbox_html)
        self.assertIn('invalid-feedback', checkbox_html)

        input_group_html = Template(
            "{% include 'core/_form_field_input_group.html' with field=form.github prefix='@' %}"
        ).render(Context({"form": invalid_form}))
        self.assertIn('class="input-group"', input_group_html)
        self.assertIn('class="input-group-prepend"', input_group_html)
        self.assertIn('class="input-group-text"', input_group_html)

        inner_html = Template(
            "{% include 'core/_form_field_inner.html' with field=form.country_code %}"
        ).render(Context({"form": invalid_form}))
        self.assertNotIn('class="form-group', inner_html)
        self.assertIn('Country help', inner_html)

    def test_phase3_templates_use_variant_includes(self) -> None:
        expectations: dict[str, list[str]] = {
            "register.html": [
                "{% include 'core/_form_field.html' with field=form.first_name wrapper_class='col-md-6' %}",
                "{% include 'core/_form_field.html' with field=form.last_name wrapper_class='col-md-6' %}",
                "{% include 'core/_form_field_checkbox.html' with field=form.over_16 %}",
            ],
            "_settings_tab_profile.html": [
                "{% include 'core/_form_field.html' with field=profile_form.country_code wrapper_class='settings-field-highlight' wrapper_attrs='id=\"country-code-field-wrapper\"' %}",
                "{% include 'core/_form_field.html' with field=profile_form.country_code wrapper_attrs='id=\"country-code-field-wrapper\"' %}",
                "{% include 'core/_form_field_input_group.html' with field=profile_form.fasGitHubUsername prefix='@' %}",
                "{% include 'core/_form_field_input_group.html' with field=profile_form.fasGitLabUsername prefix='@' %}",
                "{% include 'core/_form_field_checkbox.html' with field=profile_form.fasIsPrivate help_text_override='Hide personal details (including your name and email) from other signed-in users. Your profile stays visible, as do your groups and memberships.' %}",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)
