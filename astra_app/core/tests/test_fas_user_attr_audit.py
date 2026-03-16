from django.test import TestCase

from core.fas_user_attr_audit import _canonical_iana_timezone_name, audit_fas_user_attributes


class AuditFASUserAttributesTests(TestCase):
    def test_deprecated_timezone_alias_resolves_to_canonical_name(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Pacific").mkdir(parents=True, exist_ok=True)
            (root / "US").mkdir(parents=True, exist_ok=True)

            target = root / "Pacific" / "Honolulu"
            target.write_text("dummy", encoding="utf-8")

            link = root / "US" / "Hawaii"
            link.symlink_to(Path("../Pacific/Honolulu"))

            with patch("core.fas_user_attr_audit.zoneinfo.TZPATH", (str(root),)):
                canonical = _canonical_iana_timezone_name("US/Hawaii")

        self.assertEqual(canonical, "Pacific/Honolulu")

    def test_reports_invalid_timezone(self) -> None:
        findings = audit_fas_user_attributes(
            username="alice",
            user_data={"uid": ["alice"], "fasTimezone": ["Not/AZone"]},
        )
        self.assertTrue(any(f.attribute == "fasTimezone" and f.issue == "invalid" for f in findings))

    def test_invalid_timezone_includes_best_effort_suggestion(self) -> None:
        findings = audit_fas_user_attributes(
            username="alice",
            user_data={"uid": ["alice"], "fasTimezone": ["EST"]},
        )
        tz_findings = [f for f in findings if f.attribute == "fasTimezone" and f.issue == "invalid"]
        self.assertEqual(len(tz_findings), 1)
        self.assertTrue(bool(tz_findings[0].suggested))

    def test_reports_invalid_github_username(self) -> None:
        findings = audit_fas_user_attributes(
            username="alice",
            user_data={"uid": ["alice"], "fasGitHubUsername": ["bad handle with spaces"]},
        )
        self.assertTrue(any(f.attribute == "fasGitHubUsername" and f.issue == "invalid" for f in findings))

    def test_valid_values_produce_no_findings(self) -> None:
        findings = audit_fas_user_attributes(
            username="alice",
            user_data={
                "uid": ["alice"],
                "fasTimezone": ["UTC"],
                "fasLocale": ["en_US"],
                "fasGitHubUsername": ["octocat"],
                "fasGitLabUsername": ["octo.cat"],
                "fasWebsiteUrl": ["https://example.org"],
                "fasRssUrl": ["https://example.org/rss.xml"],
                "fasIRCNick": ["irc://libera.chat/#fedora"],
                "fasPronoun": ["they/them"],
                "fasGPGKeyId": ["0123456789ABCDEF"],
                "fasRHBZEmail": ["alice@example.org"],
            },
        )
        self.assertEqual(findings, [])

    def test_optionally_reports_non_canonical_values(self) -> None:
        findings = audit_fas_user_attributes(
            username="alice",
            user_data={"uid": ["alice"], "fasLocale": ["en_US"]},
            include_non_canonical=True,
        )
        self.assertTrue(any(f.attribute == "fasLocale" and f.issue == "non_canonical" for f in findings))
