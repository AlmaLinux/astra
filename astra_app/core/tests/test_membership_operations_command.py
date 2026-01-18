from __future__ import annotations

from unittest.mock import call, patch

from django.core.management import call_command
from django.test import TestCase


class MembershipOperationsCommandTests(TestCase):
    def test_command_runs_all_membership_jobs(self) -> None:
        with patch(
            "core.management.commands.membership_operations.call_command",
        ) as cc:
            call_command("membership_operations")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_expired_cleanup", force=False),
                call("membership_expiration_notifications", force=False),
                call("organization_sponsorship_expired_cleanup"),
                call("membership_pending_requests", force=False),
            ],
        )

    def test_force_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.membership_operations.call_command",
        ) as cc:
            call_command("membership_operations", "--force")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_expired_cleanup", force=True),
                call("membership_expiration_notifications", force=True),
                call("organization_sponsorship_expired_cleanup"),
                call("membership_pending_requests", force=True),
            ],
        )
