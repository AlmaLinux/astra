
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog


class TestSendQueuedMailCommand(SimpleTestCase):
    def test_management_command_resolves_to_core(self) -> None:
        from django.core.management import get_commands

        command_map = get_commands()

        assert command_map.get("send_queued_mail") == "core_commands"

    def test_skips_when_lock_not_acquired(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        mock_queue = MagicMock()
        mock_queue.exists.return_value = True

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (False,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(command_module, "get_queued", return_value=mock_queue),
            patch.object(command_module.PostOfficeCommand, "handle") as delegate_handle,
        ):
            command_module.Command().handle()

        delegate_handle.assert_not_called()

    def test_runs_when_lock_acquired(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(command_module, "_run_delegate_and_emit_alerts") as run_delegate,
        ):
            command_module.Command().handle(verbosity=2)

        run_delegate.assert_called_once_with(verbosity=2, log_level=2)
        # Ensure we attempted to unlock at the end.
        assert any(
            "pg_advisory_unlock" in str(call.args[0])
            for call in mock_cursor.execute.call_args_list
        )

    def test_skips_when_queue_empty(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(command_module, "_run_delegate_and_emit_alerts", return_value=0) as run_delegate,
        ):
            command_module.Command().handle()

        run_delegate.assert_called_once_with(log_level=2)
        assert any(
            "pg_advisory_unlock" in str(call.args[0])
            for call in mock_cursor.execute.call_args_list
        )

    def test_defaults_log_level_to_verbose(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(command_module, "_run_delegate_and_emit_alerts") as run_delegate,
        ):
            command_module.Command().handle()

        run_delegate.assert_called_once_with(log_level=2)


class TestSendQueuedMailQuotaAlerts(TestCase):
    def test_command_logs_daily_quota_failure_from_bulk_created_logs(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com,bob@example.org",
            subject="Quota test",
            message="top secret message body",
            status=POST_OFFICE_STATUS.queued,
            number_of_retries=2,
            backend_alias="default",
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        def bulk_create_failed_log(*args: object, **kwargs: object) -> int:
            PostOfficeLog.objects.bulk_create(
                [
                    PostOfficeLog(
                        email=email,
                        status=POST_OFFICE_STATUS.failed,
                        exception_type="ClientError",
                        message=(
                            "An error occurred (TooManyRequestsException) when calling "
                            "the SendRawEmail operation: Daily message quota exceeded"
                        ),
                    )
                ]
            )
            return 0

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(
                command_module,
                "get_queued",
                return_value=PostOfficeEmail.objects.filter(pk=email.pk),
            ),
            patch.object(
                command_module.PostOfficeCommand,
                "handle",
                side_effect=bulk_create_failed_log,
            ),
            patch("core.post_office_alerts.logger.warning") as warning_mock,
        ):
            command_module.Command().handle()

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(args[0], "astra.email.ses.send_failed")
        self.assertEqual(extra.get("event"), "astra.email.ses.send_failed")
        self.assertEqual(extra.get("post_office_email_id"), email.pk)
        self.assertEqual(extra.get("ses_error_code"), "TooManyRequestsException")
        self.assertEqual(extra.get("ses_failure_kind"), "daily_quota_exceeded")
        self.assertEqual(
            extra.get("recipient_addresses"),
            ["alice@example.com", "bob@example.org"],
        )
        self.assertEqual(extra.get("failure_stage"), "send_attempt")
        self.assertFalse(extra.get("is_post_acceptance_event"))

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("top secret message body", serialized_payload)

    def test_command_logs_daily_quota_failure_for_later_batch_email_created_during_delegate_run(
        self,
    ) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        initial_email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="initial@example.com",
            subject="Initial batch",
            message="initial message body",
            status=POST_OFFICE_STATUS.queued,
            number_of_retries=0,
            backend_alias="default",
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        def bulk_create_later_batch_failed_log(*args: object, **kwargs: object) -> int:
            later_batch_email = PostOfficeEmail.objects.create(
                from_email="from@example.com",
                to="later@example.net,later-two@example.org",
                subject="Later batch",
                message="later batch private body",
                status=POST_OFFICE_STATUS.queued,
                number_of_retries=3,
                backend_alias="default",
            )
            PostOfficeLog.objects.bulk_create(
                [
                    PostOfficeLog(
                        email=later_batch_email,
                        status=POST_OFFICE_STATUS.failed,
                        exception_type="ClientError",
                        message=(
                            "An error occurred (TooManyRequestsException) when calling "
                            "the SendRawEmail operation: Daily message quota exceeded"
                        ),
                    )
                ]
            )
            return 0

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(
                command_module,
                "get_queued",
                return_value=PostOfficeEmail.objects.filter(pk=initial_email.pk),
            ),
            patch.object(
                command_module.PostOfficeCommand,
                "handle",
                side_effect=bulk_create_later_batch_failed_log,
            ),
            patch("core.post_office_alerts.logger.warning") as warning_mock,
        ):
            command_module.Command().handle()

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(args[0], "astra.email.ses.send_failed")
        self.assertEqual(extra.get("event"), "astra.email.ses.send_failed")
        self.assertNotEqual(extra.get("post_office_email_id"), initial_email.pk)
        self.assertEqual(extra.get("ses_error_code"), "TooManyRequestsException")
        self.assertEqual(extra.get("ses_failure_kind"), "daily_quota_exceeded")
        self.assertEqual(
            extra.get("recipient_addresses"),
            ["later-two@example.org", "later@example.net"],
        )
        self.assertEqual(extra.get("failure_stage"), "send_attempt")
        self.assertFalse(extra.get("is_post_acceptance_event"))

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("later batch private body", serialized_payload)

    def test_command_logs_non_quota_ses_failure_from_bulk_created_logs(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com",
            subject="Rejected test",
            message="private message body",
            status=POST_OFFICE_STATUS.queued,
            number_of_retries=1,
            backend_alias="default",
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        def bulk_create_failed_log(*args: object, **kwargs: object) -> int:
            PostOfficeLog.objects.bulk_create(
                [
                    PostOfficeLog(
                        email=email,
                        status=POST_OFFICE_STATUS.failed,
                        exception_type="ClientError",
                        message=(
                            "An error occurred (MessageRejected) when calling "
                            "the SendRawEmail operation: Email address is not verified"
                        ),
                    )
                ]
            )
            return 0

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(
                command_module,
                "get_queued",
                return_value=PostOfficeEmail.objects.filter(pk=email.pk),
            ),
            patch.object(
                command_module.PostOfficeCommand,
                "handle",
                side_effect=bulk_create_failed_log,
            ),
            patch("core.post_office_alerts.logger.warning") as warning_mock,
        ):
            command_module.Command().handle()

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(args[0], "astra.email.ses.send_failed")
        self.assertEqual(extra.get("event"), "astra.email.ses.send_failed")
        self.assertEqual(extra.get("ses_failure_kind"), "other")
        self.assertEqual(extra.get("ses_error_code"), "MessageRejected")
        self.assertEqual(extra.get("exception_type"), "ClientError")
        self.assertEqual(extra.get("recipient_addresses"), ["alice@example.com"])
        self.assertEqual(extra.get("failure_stage"), "send_attempt")
        self.assertFalse(extra.get("is_post_acceptance_event"))

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("private message body", serialized_payload)

    def test_command_does_not_log_non_ses_failure_from_bulk_created_logs(self) -> None:
        from core_commands.management.commands import send_queued_mail as command_module

        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com",
            subject="SMTP failure",
            message="private message body",
            status=POST_OFFICE_STATUS.queued,
            number_of_retries=1,
            backend_alias="default",
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)

        mock_connection = MagicMock()
        mock_connection.vendor = "postgresql"
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        def bulk_create_failed_log(*args: object, **kwargs: object) -> int:
            PostOfficeLog.objects.bulk_create(
                [
                    PostOfficeLog(
                        email=email,
                        status=POST_OFFICE_STATUS.failed,
                        exception_type="SMTPRecipientsRefused",
                        message="SMTPRecipientsRefused: {'alice@example.com': (550, b'User unknown')}",
                    )
                ]
            )
            return 0

        with (
            patch.object(command_module, "connection", mock_connection),
            patch.object(
                command_module,
                "get_queued",
                return_value=PostOfficeEmail.objects.filter(pk=email.pk),
            ),
            patch.object(
                command_module.PostOfficeCommand,
                "handle",
                side_effect=bulk_create_failed_log,
            ),
            patch("core.post_office_alerts.logger.warning") as warning_mock,
        ):
            command_module.Command().handle()

        warning_mock.assert_not_called()
