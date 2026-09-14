import datetime
from io import StringIO
from unittest.mock import call, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core import mail_progress
from core.freeipa.user import FreeIPAUser
from core.mail_progress import MailRunKind
from core.models import (
    AuditLogEntry,
    Candidate,
    Election,
    ElectionRoll,
    Membership,
    MembershipType,
    VotingCredential,
)


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
                call("freeipa_team_leads_sync", dry_run=False),
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
                call("freeipa_team_leads_sync", dry_run=False),
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
                call("freeipa_team_leads_sync", dry_run=True),
                call("selfservice_lifecycle_cleanup", dry_run=True),
                call("account_invitations_refresh"),
            ],
        )


class OperationsWeeklyCommandTests(TestCase):
    def test_command_runs_weekly_jobs(self) -> None:
        with (
            patch("core.management.commands.operations_weekly.call_command") as cc,
            self.assertLogs("core.management.commands.operations_weekly", level="INFO") as logs,
        ):
            call_command("operations_weekly")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_pending_requests", force=False, dry_run=False),
                call("membership_embargoed_members", force=False, dry_run=False),
            ],
        )
        self.assertTrue(
            any("operations_weekly" in line for line in logs.output),
            f"Expected weekly operations logs, got: {logs.output}",
        )

    def test_force_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.operations_weekly.call_command",
        ) as cc:
            call_command("operations_weekly", "--force")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_pending_requests", force=True, dry_run=False),
                call("membership_embargoed_members", force=True, dry_run=False),
            ],
        )

    def test_dry_run_is_passed_through(self) -> None:
        with patch(
            "core.management.commands.operations_weekly.call_command",
        ) as cc:
            call_command("operations_weekly", "--dry-run")

        self.assertEqual(
            cc.mock_calls,
            [
                call("membership_pending_requests", force=False, dry_run=True),
                call("membership_embargoed_members", force=False, dry_run=True),
            ],
        )


class OperationsHourlyCommandTests(TestCase):
    def test_election_lifecycle_automation_reports_empty_scan(self) -> None:
        output = StringIO()

        call_command("election_lifecycle_automation", verbosity=3, stdout=output)

        self.assertIn("Due automatic starts: 0", output.getvalue())
        self.assertIn("Due automatic ends: 0", output.getvalue())

    def test_election_lifecycle_automation_reports_due_start_schedule(self) -> None:
        scheduled_for = timezone.now() - datetime.timedelta(hours=1)
        Election.objects.create(
            name="Scheduled diagnostic",
            description="",
            start_datetime=scheduled_for,
            end_datetime=scheduled_for + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
            auto_start_enabled=True,
        )
        output = StringIO()

        call_command("election_lifecycle_automation", "--dry-run", verbosity=3, stdout=output)

        self.assertIn(f"scheduled start: {scheduled_for.isoformat()}", output.getvalue())

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
    def test_election_lifecycle_automation_starts_a_due_election_end_to_end(self) -> None:
        """The cron path opens, issues and emails without any progress record.

        Progress reporting exists for operators watching a browser; an automatic
        start has nobody watching, so it runs synchronously in the command.
        """
        now = timezone.now()
        scheduled_for = now - datetime.timedelta(hours=1)
        election = Election.objects.create(
            name="Scheduled start",
            description="",
            start_datetime=scheduled_for,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
            auto_start_enabled=True,
            voting_email_subject="Hello {{ username }}",
            voting_email_html="<p>Hi {{ username }}</p>",
            voting_email_text="Hi {{ username }}",
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        membership_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            description="",
            category_id="individual",
            sort_order=1,
            enabled=True,
            votes=1,
        )
        for username in ("voter1", "voter2", "alice", "nominator"):
            membership = Membership.objects.create(
                target_username=username,
                membership_type=membership_type,
                expires_at=None,
            )
            Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=200))

        def _get_user(username: str, **_: object) -> FreeIPAUser:
            return FreeIPAUser(
                username,
                {"uid": [username], "memberof_group": [], "mail": [f"{username}@example.com"]},
            )

        output = StringIO()
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.elections_services.send_voting_credential_email", autospec=True, return_value=None) as send_mock,
            patch("core.mail_progress._spawn", side_effect=AssertionError("automatic starts must not spawn a delivery thread")),
        ):
            call_command("election_lifecycle_automation", verbosity=2, stdout=output)

        self.assertIn("start started", output.getvalue())

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.open)
        # Cleared so a later run cannot start the same election twice.
        self.assertFalse(election.auto_start_enabled)

        self.assertEqual(VotingCredential.objects.filter(election=election).count(), 4)
        self.assertEqual(ElectionRoll.objects.filter(election=election).count(), 4)
        self.assertEqual(send_mock.call_count, 4)

        audit = AuditLogEntry.objects.get(election=election, event_type="election_started")
        self.assertEqual(audit.payload["actor"], "operations_hourly")
        self.assertTrue(audit.payload["automation"])
        self.assertEqual(audit.payload["emailed"], 4)
        self.assertEqual(audit.payload["failures"], 0)

        # No operator is watching, so no progress record is kept.
        self.assertIsNone(mail_progress.read(scope=str(election.id), kind=MailRunKind.election_start))

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
    def test_election_lifecycle_automation_does_not_restart_an_open_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Already started",
            description="",
            start_datetime=now - datetime.timedelta(hours=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            auto_start_enabled=True,
        )
        output = StringIO()

        with patch("core.elections_services.send_voting_credential_email", autospec=True) as send_mock:
            call_command("election_lifecycle_automation", verbosity=2, stdout=output)

        send_mock.assert_not_called()
        self.assertEqual(VotingCredential.objects.filter(election=election).count(), 0)

    def test_election_lifecycle_automation_closes_due_enabled_no_quorum_election(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Scheduled close",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now - datetime.timedelta(hours=1),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.open,
        )

        Election.objects.filter(pk=election.pk).update(auto_end_enabled=True)

        call_command("election_lifecycle_automation")

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)
        self.assertFalse(election.auto_end_enabled)

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
                call("election_lifecycle_automation", force=False, dry_run=False),
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
                call("election_lifecycle_automation", force=True, dry_run=False),
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
                call("election_lifecycle_automation", force=False, dry_run=True),
            ],
        )