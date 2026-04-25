import re
from pathlib import Path

from django.test import SimpleTestCase


class ViteEntrypointContractTests(SimpleTestCase):
    def test_template_vite_assets_have_source_files_and_dynamic_build_inputs(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        templates_root = repo_root / "astra_app" / "core" / "templates"
        vite_config_path = repo_root / "frontend" / "vite.config.ts"
        frontend_root = repo_root / "frontend"

        vite_asset_pattern = re.compile(r"{%\s*vite_asset\s+['\"](?P<entry>src/entrypoints/[^'\"]+\.ts)['\"]\s*%}")

        template_entries: set[str] = set()
        for template_path in templates_root.rglob("*.html"):
            template_text = template_path.read_text(encoding="utf-8")
            template_entries.update(match.group("entry") for match in vite_asset_pattern.finditer(template_text))

        self.assertTrue(template_entries, "Expected at least one vite_asset entrypoint reference in templates.")

        vite_config_text = vite_config_path.read_text(encoding="utf-8")
        self.assertIn("buildEntrypointInputs(new URL(\"./\", import.meta.url))", vite_config_text)

        missing_files = sorted(
            entry for entry in template_entries if not (frontend_root / entry).exists()
        )
        self.assertEqual(
            missing_files,
            [],
            "Template vite_asset entrypoints with no source file under frontend/: "
            f"{missing_files}",
        )
