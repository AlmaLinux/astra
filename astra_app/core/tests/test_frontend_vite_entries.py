import re
from pathlib import Path

from django.test import SimpleTestCase


class FrontendViteEntriesContractTests(SimpleTestCase):
    def test_all_vite_build_input_entrypoints_are_listed_in_templates(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        template_assets = self._collect_template_vite_assets(repository_root=repository_root)
        vite_inputs = self._collect_vite_config_entrypoints(repository_root=repository_root)

        missing_assets = sorted(vite_inputs - template_assets)
        self.assertEqual(
            missing_assets,
            [],
            msg=(
                "frontend/vite.config.ts rollupOptions.input contains entrypoints not listed in template "
                "vite_asset tags: "
                f"rollupOptions.input: {', '.join(missing_assets)}"
            ),
        )

    def _collect_template_vite_assets(self, *, repository_root: Path) -> set[str]:
        templates_root = repository_root / "astra_app"
        vite_asset_pattern = re.compile(r"{%\s*vite_asset\s+['\"]([^'\"]+)['\"]\s*%}")
        assets: set[str] = set()

        for template_path in templates_root.rglob("*.html"):
            template_content = template_path.read_text(encoding="utf-8")
            assets.update(vite_asset_pattern.findall(template_content))

        return assets

    def _collect_vite_config_entrypoints(self, *, repository_root: Path) -> set[str]:
        vite_config_path = repository_root / "frontend" / "vite.config.ts"
        vite_config = vite_config_path.read_text(encoding="utf-8")
        entrypoint_pattern = re.compile(r"\.\/src\/entrypoints\/[^'\"]+\.ts")
        return {entry.removeprefix("./") for entry in entrypoint_pattern.findall(vite_config)}
