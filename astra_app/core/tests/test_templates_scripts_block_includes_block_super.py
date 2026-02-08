
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class TemplatesScriptsBlockIncludesBlockSuperTests(SimpleTestCase):
    def test_templates_overriding_scripts_include_block_super(self) -> None:
        """Regression: templates must not shadow the base scripts block.

        Pages that override `{% block scripts %}` but omit `{{ block.super }}` drop
        AdminLTE/Bootstrap/jQuery and any base scripts, leading to page-specific
        UI and layout glitches.
        """

        templates_root = Path(settings.BASE_DIR) / "core" / "templates"
        self.assertTrue(templates_root.exists(), f"Missing templates dir: {templates_root}")

        start_tag = "{% block scripts %}"
        end_tag = "{% endblock %}"

        def find_script_blocks(text: str) -> list[str]:
            blocks: list[str] = []
            start = 0
            while True:
                i = text.find(start_tag, start)
                if i == -1:
                    return blocks
                j = text.find(end_tag, i + len(start_tag))
                if j == -1:
                    blocks.append(text[i:])
                    return blocks
                blocks.append(text[i : j + len(end_tag)])
                start = j + len(end_tag)

        bad: list[Path] = []

        for path in sorted(templates_root.rglob("*.html")):
            # base.html defines the scripts block; it should not include block.super.
            if path.name == "base.html" and path.parent.name == "core":
                continue

            text = path.read_text(encoding="utf-8")

            # Only enforce for templates that extend the base layout.
            if "{% extends 'core/base.html' %}" not in text and '{% extends "core/base.html" %}' not in text:
                continue

            blocks = find_script_blocks(text)
            if not blocks:
                continue

            if any("{{ block.super }}" not in b for b in blocks):
                bad.append(path)

        if bad:
            rel = [str(p.relative_to(templates_root)) for p in bad]
            self.fail("Templates overriding scripts without block.super: " + ", ".join(rel))
