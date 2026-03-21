from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase


class _DummyFreeIPAClient:
    def __init__(self, *, users: list[dict[str, object]]) -> None:
        self._users = users

    def user_find(self, **_kwargs: object) -> dict[str, object]:
        return {"result": self._users}


class AuditFasUserAttrsFixCommandTests(TestCase):
    def test_fix_applies_non_canonical_and_high_confidence_timezone(self) -> None:
        users: list[dict[str, object]] = [
            {
                "uid": ["alice"],
                "fasLocale": ["en_US"],
                "fasGitHubUsername": ["https://github.com/octocat"],
                "fasTimezone": ["utc"],
            },
            {
                "uid": ["bob"],
                # Abbreviation suggestions are inherently ambiguous; the command should not fix these.
                "fasTimezone": ["EST"],
            },
        ]
        dummy_client = _DummyFreeIPAClient(users=users)

        with (
            self.assertLogs("core_commands.management.commands.audit_fas_user_attrs", level="INFO") as logs,
            patch(
                "core_commands.management.commands.audit_fas_user_attrs.FreeIPAUser.get_client",
                return_value=dummy_client,
            ),
            patch(
                "core_commands.management.commands.audit_fas_user_attrs._is_high_confidence_timezone_suggestion",
                side_effect=lambda *, suggested, valid_timezones: suggested == "UTC",
            ),
            patch(
                "core_commands.management.commands.audit_fas_user_attrs._update_user_attrs",
                return_value=([], True),
            ) as update_mock,
        ):
            call_command("audit_fas_user_attrs", "--fix")

        update_mock.assert_called_once()
        args, kwargs = update_mock.call_args

        self.assertEqual(args[0], "alice")
        self.assertEqual(kwargs["addattrs"], [])
        self.assertEqual(kwargs["delattrs"], [])
        self.assertEqual(
            sorted(kwargs["setattrs"]),
            sorted(
                [
                    "fasGitHubUsername=octocat",
                    "fasLocale=en-US",
                    "fasTimezone=UTC",
                ]
            ),
        )

        self.assertTrue(
            any("Applied fixes for 1 user(s)" in line for line in logs.output),
            f"Expected a summary log, got: {logs.output}",
        )

    def test_fix_does_not_write_when_no_high_confidence_suggestion(self) -> None:
        users: list[dict[str, object]] = [
            {
                "uid": ["bob"],
                "fasTimezone": ["EST"],
            },
        ]
        dummy_client = _DummyFreeIPAClient(users=users)

        with (
            self.assertLogs("core_commands.management.commands.audit_fas_user_attrs", level="INFO") as logs,
            patch(
                "core_commands.management.commands.audit_fas_user_attrs.FreeIPAUser.get_client",
                return_value=dummy_client,
            ),
            patch(
                "core_commands.management.commands.audit_fas_user_attrs._is_high_confidence_timezone_suggestion",
                return_value=False,
            ),
            patch(
                "core_commands.management.commands.audit_fas_user_attrs._update_user_attrs",
                return_value=([], True),
            ) as update_mock,
        ):
            call_command("audit_fas_user_attrs", "--fix")

        update_mock.assert_not_called()
        self.assertTrue(
            any("Applied fixes for 0 user(s)" in line for line in logs.output),
            f"Expected a summary log, got: {logs.output}",
        )
