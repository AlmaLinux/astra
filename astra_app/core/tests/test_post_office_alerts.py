from unittest.mock import patch

from django.test import TestCase
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog


class PostOfficeQuotaAlertTests(TestCase):
    def test_daily_quota_exceeded_failed_delivery_emits_structured_warning(self) -> None:
        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com,bob@example.org",
            subject="Quota test",
            message="top secret message body",
            status=POST_OFFICE_STATUS.requeued,
            number_of_retries=2,
            backend_alias="default",
        )

        with patch("core.post_office_alerts.logger.warning") as warning_mock:
            PostOfficeLog.objects.create(
                email=email,
                status=POST_OFFICE_STATUS.failed,
                exception_type="ClientError",
                message=(
                    "An error occurred (TooManyRequestsException) when calling the "
                    "SendRawEmail operation: Daily message quota exceeded"
                ),
            )

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(args[0], "astra.email.ses.send_failed")
        self.assertEqual(extra.get("event"), "astra.email.ses.send_failed")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("provider"), "aws_ses")
        self.assertEqual(extra.get("failure_stage"), "send_attempt")
        self.assertFalse(extra.get("is_post_acceptance_event"))
        self.assertEqual(extra.get("post_office_email_id"), email.pk)
        self.assertEqual(extra.get("ses_error_code"), "TooManyRequestsException")
        self.assertEqual(extra.get("ses_failure_kind"), "daily_quota_exceeded")
        self.assertEqual(extra.get("retry_count"), 2)
        self.assertEqual(
            extra.get("recipient_addresses"),
            ["alice@example.com", "bob@example.org"],
        )

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("top secret message body", serialized_payload)

    def test_non_quota_ses_failure_emits_structured_warning(self) -> None:
        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com",
            subject="Other failure",
            message="body",
            status=POST_OFFICE_STATUS.requeued,
            number_of_retries=1,
            backend_alias="default",
        )

        with patch("core.post_office_alerts.logger.warning") as warning_mock:
            PostOfficeLog.objects.create(
                email=email,
                status=POST_OFFICE_STATUS.failed,
                exception_type="ClientError",
                message=(
                    "An error occurred (MessageRejected) when calling the SendRawEmail "
                    "operation: Email address is not verified"
                ),
            )

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(args[0], "astra.email.ses.send_failed")
        self.assertEqual(extra.get("event"), "astra.email.ses.send_failed")
        self.assertEqual(extra.get("ses_failure_kind"), "other")
        self.assertEqual(extra.get("ses_error_code"), "MessageRejected")
        self.assertEqual(extra.get("exception_type"), "ClientError")
        self.assertEqual(extra.get("failure_stage"), "send_attempt")
        self.assertFalse(extra.get("is_post_acceptance_event"))
        self.assertEqual(extra.get("recipient_addresses"), ["alice@example.com"])

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("body", serialized_payload)

    def test_non_ses_failure_does_not_emit_structured_warning(self) -> None:
        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com",
            subject="SMTP failure",
            message="private body",
            status=POST_OFFICE_STATUS.requeued,
            number_of_retries=1,
            backend_alias="default",
        )

        with patch("core.post_office_alerts.logger.warning") as warning_mock:
            PostOfficeLog.objects.create(
                email=email,
                status=POST_OFFICE_STATUS.failed,
                exception_type="SMTPRecipientsRefused",
                message="SMTPRecipientsRefused: {'alice@example.com': (550, b'User unknown')}",
            )

        warning_mock.assert_not_called()