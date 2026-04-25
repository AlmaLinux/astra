import re
from pathlib import Path

from django.test import SimpleTestCase


class FrontendViteEntriesContractTests(SimpleTestCase):
    def test_all_entrypoint_files_are_listed_in_templates(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        template_assets = self._collect_template_vite_assets(repository_root=repository_root)
        entrypoint_files = self._collect_entrypoint_files(repository_root=repository_root)

        missing_assets = sorted(entrypoint_files - template_assets)
        self.assertEqual(
            missing_assets,
            [],
            msg=(
                "frontend/src/entrypoints contains entrypoints not listed in template "
                "vite_asset tags: "
                f"entrypoints: {', '.join(missing_assets)}"
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

    def _collect_entrypoint_files(self, *, repository_root: Path) -> set[str]:
        entrypoints_root = repository_root / "frontend" / "src" / "entrypoints"
        return {
            f"src/entrypoints/{entrypoint_path.name}"
            for entrypoint_path in entrypoints_root.glob("*.ts")
        }
