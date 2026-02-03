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
                call("membership_expired_cleanup", force=False, dry_run=False),
                call("membership_expiration_notifications", force=False, dry_run=False),
                call("freeipa_membership_reconcile", report=True, dry_run=False),
                call("membership_pending_requests", force=False, dry_run=False),
                call("membership_embargoed_members", force=False, dry_run=False),
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
                call("membership_expired_cleanup", force=True, dry_run=False),
                call("membership_expiration_notifications", force=True, dry_run=False),
                call("freeipa_membership_reconcile", report=True, dry_run=False),
                call("membership_pending_requests", force=True, dry_run=False),
                call("membership_embargoed_members", force=True, dry_run=False),
            ],
        )

    def test_dry_run_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.membership_operations.call_command",
        ) as cc:
            call_command("membership_operations", "--dry-run")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_expired_cleanup", force=False, dry_run=True),
                call("membership_expiration_notifications", force=False, dry_run=True),
                call("freeipa_membership_reconcile", report=True, dry_run=True),
                call("membership_pending_requests", force=False, dry_run=True),
                call("membership_embargoed_members", force=False, dry_run=True),
            ],
        )
