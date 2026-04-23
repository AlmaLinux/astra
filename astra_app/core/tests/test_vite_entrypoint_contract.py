import re
from pathlib import Path

from django.test import SimpleTestCase


class ViteEntrypointContractTests(SimpleTestCase):
    def test_template_vite_assets_are_declared_in_vite_build_inputs(self) -> None:
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
        config_entries = {
            match
            for match in re.findall(
                r"\"\./(src/entrypoints/[^\"]+\.ts)\"|\'\./(src/entrypoints/[^\']+\.ts)\'",
                vite_config_text,
            )
            for match in match
            if match
        }

        self.assertTrue(config_entries, "Expected at least one Vite build input entrypoint in vite.config.ts.")

        missing_from_config = sorted(template_entries - config_entries)
        self.assertEqual(
            missing_from_config,
            [],
            "Template vite_asset entrypoints missing from frontend/vite.config.ts build.rollupOptions.input: "
            f"{missing_from_config}",
        )

        missing_files = sorted(
            entry for entry in template_entries if not (frontend_root / entry).exists()
        )
        self.assertEqual(
            missing_files,
            [],
            "Template vite_asset entrypoints with no source file under frontend/: "
            f"{missing_files}",
        )
