from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase


class _Phase3Form(forms.Form):
    over_16 = forms.BooleanField(required=True, label="I am over 16")
    country_code = forms.CharField(help_text="Country help")
    github = forms.CharField()


class FormFieldDryPhase3VariantTests(SimpleTestCase):
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
