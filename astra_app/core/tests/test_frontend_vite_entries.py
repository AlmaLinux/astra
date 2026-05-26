import re
from pathlib import Path

from django.test import SimpleTestCase


class FrontendViteEntriesContractTests(SimpleTestCase):
    def test_literal_template_vite_assets_reference_existing_entrypoints(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        template_assets = self._collect_literal_template_vite_assets(repository_root=repository_root)
        entrypoint_files = self._collect_entrypoint_files(repository_root=repository_root)

        missing_assets = sorted(template_assets - entrypoint_files)
        self.assertEqual(
            missing_assets,
            [],
            msg=(
                "Literal template vite_asset references point to entrypoints that do not exist under "
                "frontend/src/entrypoints: "
                f"assets: {', '.join(missing_assets)}"
            ),
        )

    def _collect_literal_template_vite_assets(self, *, repository_root: Path) -> set[str]:
        templates_root = repository_root / "astra_app"
        assets: set[str] = set()

        for template_path in templates_root.rglob("*.html"):
            template_content = template_path.read_text(encoding="utf-8")
            for asset_path in re.findall(r"vite_asset\s+['\"]([^'\"]+)['\"]", template_content):
                assets.add(asset_path)

        return assets

    def _collect_entrypoint_files(self, *, repository_root: Path) -> set[str]:
        entrypoints_root = repository_root / "frontend" / "src" / "entrypoints"
        return {
            f"src/entrypoints/{entrypoint_path.name}"
            for entrypoint_path in entrypoints_root.glob("*.ts")
        }
