"""Tests for the make_presets template tag."""

from django.template import Context, Template
from django.test import SimpleTestCase


class MakePresetsTagTests(SimpleTestCase):
    """Verify the make_presets template tag builds a list of {label, value} dicts."""

    def test_produces_list_of_dicts(self) -> None:
        tpl = Template(
            '{% load core_dict %}'
            '{% make_presets "Lbl1" "Val1" "Lbl2" "Val2" as presets %}'
            '{{ presets }}'
        )
        rendered = tpl.render(Context())
        # The tag should produce a Python list of dicts
        self.assertIn("Lbl1", rendered)
        self.assertIn("Val1", rendered)

    def test_empty_call(self) -> None:
        tpl = Template(
            '{% load core_dict %}'
            '{% make_presets as presets %}'
            '{{ presets|length }}'
        )
        rendered = tpl.render(Context()).strip()
        self.assertEqual(rendered, "0")

    def test_preset_iteration(self) -> None:
        """Confirm presets can be iterated and accessed like dicts."""
        tpl = Template(
            '{% load core_dict %}'
            '{% make_presets "Alpha" "a-value" "Beta" "b-value" as presets %}'
            '{% for p in presets %}{{ p.label }}:{{ p.value }};{% endfor %}'
        )
        rendered = tpl.render(Context())
        self.assertEqual(rendered, "Alpha:a-value;Beta:b-value;")
