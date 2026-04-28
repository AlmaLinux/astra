from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase
class _SampleForm(forms.Form):
    name = forms.CharField(help_text="Original help")


class FormFieldDryPhase2TemplateTests(SimpleTestCase):
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
        self.assertIn("invalid-feedback", rendered)
        self.assertNotIn("Original help", rendered)
        self.assertIn("Custom helper text", rendered)
