from unittest.mock import call, patch

from django.core.management import call_command
from django.test import TestCase


class OperationsDailyCommandTests(TestCase):
    def test_command_runs_daily_jobs(self) -> None:
        with (
            patch("core.management.commands.operations_daily.call_command") as cc,
            self.assertLogs("core.management.commands.operations_daily", level="INFO") as logs,
        ):
            call_command("operations_daily")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_expired_cleanup", force=False, dry_run=False),
                call("membership_expiration_notifications", force=False, dry_run=False),
                call("freeipa_membership_reconcile", report=True, dry_run=False),
                call("membership_pending_requests", force=False, dry_run=False),
                call("membership_embargoed_members", force=False, dry_run=False),
                call("selfservice_lifecycle_cleanup", dry_run=False),
                call("account_invitations_refresh"),
            ],
        )
        self.assertTrue(
            any("operations_daily" in line for line in logs.output),
            f"Expected daily operations logs, got: {logs.output}",
        )

    def test_force_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.operations_daily.call_command",
        ) as cc:
            call_command("operations_daily", "--force")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_expired_cleanup", force=True, dry_run=False),
                call("membership_expiration_notifications", force=True, dry_run=False),
                call("freeipa_membership_reconcile", report=True, dry_run=False),
                call("membership_pending_requests", force=True, dry_run=False),
                call("membership_embargoed_members", force=True, dry_run=False),
                call("selfservice_lifecycle_cleanup", dry_run=False),
                call("account_invitations_refresh"),
            ],
        )

    def test_dry_run_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.operations_daily.call_command",
        ) as cc:
            call_command("operations_daily", "--dry-run")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_expired_cleanup", force=False, dry_run=True),
                call("membership_expiration_notifications", force=False, dry_run=True),
                call("freeipa_membership_reconcile", report=True, dry_run=True),
                call("membership_pending_requests", force=False, dry_run=True),
                call("membership_embargoed_members", force=False, dry_run=True),
                call("selfservice_lifecycle_cleanup", dry_run=True),
                call("account_invitations_refresh"),
            ],
        )


class OperationsHourlyCommandTests(TestCase):
    def test_command_runs_hourly_jobs(self) -> None:
        with (
            patch("core.management.commands.operations_hourly.call_command") as cc,
            self.assertLogs("core.management.commands.operations_hourly", level="INFO") as logs,
        ):
            call_command("operations_hourly")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_mirror_validation", force=False, dry_run=False),
            ],
        )
        self.assertTrue(
            any("operations_hourly" in line for line in logs.output),
            f"Expected hourly operations logs, got: {logs.output}",
        )

    def test_force_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.operations_hourly.call_command",
        ) as cc:
            call_command("operations_hourly", "--force")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_mirror_validation", force=True, dry_run=False),
            ],
        )

    def test_dry_run_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.operations_hourly.call_command",
        ) as cc:
            call_command("operations_hourly", "--dry-run")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_mirror_validation", force=False, dry_run=True),
            ],
        )