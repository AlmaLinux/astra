from pathlib import Path

from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase

from core.forms_base import StyledForm


class _Plan093ValidationForm(StyledForm):
    required_text = forms.CharField(required=True)
    optional_text = forms.CharField(required=False)
    required_grouped = forms.CharField(required=True)
    required_datalist = forms.CharField(required=True, widget=forms.TextInput(attrs={"list": "plan093-list"}))
    required_checkbox = forms.BooleanField(required=True, label="Confirm")


class Plan093RealtimeValidationTests(SimpleTestCase):
    def _read_core_static(self, *relative_parts: str) -> str:
        path = Path(__file__).resolve().parents[1] / "static" / "core"
        for part in relative_parts:
            path /= part
        return path.read_text(encoding="utf-8")

    def test_required_indicator_renders_for_shared_field_variants(self) -> None:
        form = _Plan093ValidationForm()

        inner_required_html = Template(
            "{% include 'core/_form_field_inner.html' with field=form.required_text %}"
        ).render(Context({"form": form}))
        inner_optional_html = Template(
            "{% include 'core/_form_field_inner.html' with field=form.optional_text %}"
        ).render(Context({"form": form}))
        input_group_html = Template(
            "{% include 'core/_form_field_input_group.html' with field=form.required_grouped prefix='@' %}"
        ).render(Context({"form": form}))
        datalist_html = Template(
            "{% include 'core/_form_field_datalist.html' with field=form.required_datalist datalist_id='plan093-list' datalist_options=datalist_options %}"
        ).render(Context({"form": form, "datalist_options": [("UTC", "UTC")]}))
        fallback_html = Template(
            "{% include 'core/_form_field_widget_fallback.html' with field=form.required_text widget_id='widget-id' fallback_id='fallback-id' %}"
        ).render(Context({"form": form}))
        checkbox_html = Template(
            "{% include 'core/_form_field_checkbox.html' with field=form.required_checkbox %}"
        ).render(Context({"form": form}))

        self.assertIn('data-required-indicator-for="id_required_text"', inner_required_html)
        self.assertIn(
            'data-required-indicator-for="id_optional_text" class="form-required-indicator',
            inner_optional_html,
        )
        self.assertIn('form-required-indicator d-none', inner_optional_html)
        self.assertIn('data-required-indicator-for="id_required_grouped"', input_group_html)
        self.assertIn('data-required-indicator-for="id_required_datalist"', datalist_html)
        self.assertIn('data-required-indicator-for="id_required_text"', fallback_html)
        self.assertIn('data-required-indicator-for="id_required_checkbox"', checkbox_html)

    def test_styled_form_sets_required_widget_attribute_for_required_fields(self) -> None:
        form = _Plan093ValidationForm()

        self.assertEqual(form.fields["required_text"].widget.attrs.get("required"), "required")
        self.assertNotIn("required", form.fields["optional_text"].widget.attrs)
        self.assertEqual(form.fields["required_checkbox"].widget.attrs.get("required"), "required")

    def test_realtime_validation_js_uses_touched_blur_and_invalid_only_states(self) -> None:
        script = self._read_core_static("js", "form_validation_bootstrap44.js")

        self.assertIn('field.dataset.astraTouched = "1"', script)
        self.assertIn('field.classList.toggle("is-invalid", !field.checkValidity())', script)
        self.assertIn('field.dataset.astraTouched === "1" || field.classList.contains("is-invalid")', script)
        self.assertNotIn('classList.add("is-valid")', script)

    def test_base_css_displays_invalid_feedback_when_field_is_invalid_without_form_was_validated(self) -> None:
        css = self._read_core_static("css", "base.css")

        self.assertIn('.form-control.is-invalid ~ .invalid-feedback', css)
        self.assertIn('.form-check-input.is-invalid ~ .invalid-feedback', css)
        self.assertIn('.input-group .form-control.is-invalid ~ .invalid-feedback', css)
