from pathlib import Path

from django.test import SimpleTestCase


class ElectionsLegacyCleanupTests(SimpleTestCase):
    def test_migrated_election_pages_do_not_ship_legacy_static_chart_scripts(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]

        legacy_static_paths = [
            repo_root / "astra_app" / "core" / "static" / "core" / "js" / "election_sankey.js",
            repo_root / "astra_app" / "core" / "static" / "core" / "js" / "election_turnout_chart.js",
            repo_root / "astra_app" / "core" / "templates" / "core" / "_modal_name_confirm.html",
            repo_root / "astra_app" / "core" / "templates" / "core" / "_modal_conclude_election_body.html",
            repo_root / "astra_app" / "core" / "templates" / "core" / "_modal_extend_election_body.html",
        ]
        self.assertEqual([path for path in legacy_static_paths if path.exists()], [])

        template_paths = [
            repo_root / "astra_app" / "core" / "templates" / "core" / "election_audit_log.html",
            repo_root / "astra_app" / "core" / "templates" / "core" / "election_detail.html",
            repo_root / "astra_app" / "core" / "templates" / "core" / "debug_sankey.html",
        ]
        forbidden_tokens = [
            "core/js/election_sankey.js",
            "core/js/election_turnout_chart.js",
            "tally-sankey-data",
            "election-turnout-chart-data",
            "_modal_name_confirm.html",
            "bindNameConfirm",
        ]

        violations = []
        for template_path in template_paths:
            template = template_path.read_text(encoding="utf-8")
            violations.extend(
                f"{template_path.relative_to(repo_root)} contains {token}"
                for token in forbidden_tokens
                if token in template
            )

        self.assertEqual(violations, [])