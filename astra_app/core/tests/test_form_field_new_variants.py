from pathlib import Path

from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase


class _VariantForm(forms.Form):
    timezone = forms.CharField(
        required=False,
        help_text="Timezone help",
        widget=forms.TextInput(attrs={"list": "timezone-options"}),
    )
    website = forms.CharField(required=False, help_text="Website help", widget=forms.Textarea())


class FormFieldNewVariantsTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_new_variant_templates_render_expected_patterns(self) -> None:
        form = _VariantForm(data={"timezone": "", "website": ""})
        form.is_valid()

        datalist_html = Template(
            """
            {% include 'core/_form_field_datalist.html' with field=form.timezone datalist_id='timezone-options' datalist_options=datalist_options %}
            """
        ).render(Context({"form": form, "datalist_options": [("UTC", "UTC"), ("Europe/Zurich", "Europe/Zurich")]}))
        self.assertIn("datalist", datalist_html)
        self.assertIn('id="timezone-options"', datalist_html)
        self.assertIn('value="UTC"', datalist_html)
        self.assertIn("Timezone help", datalist_html)

        widget_html = Template(
            """
            {% include 'core/_form_field_widget_fallback.html' with field=form.website widget_id='website-widget' fallback_id='website-fallback' data_textarea_id='id_website' data_fallback_id='website-fallback' table_id='website-table' add_button_id='website-add' add_button_title='Add another website URL' add_button_text='Add website URL' %}
            """
        ).render(Context({"form": form}))
        self.assertIn('id="website-widget"', widget_html)
        self.assertIn('data-textarea-id="id_website"', widget_html)
        self.assertIn('class="d-none"', widget_html)
        self.assertIn('id="website-fallback"', widget_html)
        self.assertIn("Website help", widget_html)

