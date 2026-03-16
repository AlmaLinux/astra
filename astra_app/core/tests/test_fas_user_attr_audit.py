from django.test import TestCase

from core.fas_user_attr_audit import audit_fas_user_attributes


class AuditFASUserAttributesTests(TestCase):
    def test_reports_invalid_timezone(self) -> None:
        findings = audit_fas_user_attributes(
            username="alice",
            user_data={"uid": ["alice"], "fasTimezone": ["Not/AZone"]},
        )
        self.assertTrue(any(f.attribute == "fasTimezone" and f.issue == "invalid" for f in findings))

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
