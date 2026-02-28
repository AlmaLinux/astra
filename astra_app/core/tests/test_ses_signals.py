from unittest.mock import patch

from django.contrib import admin
from django.test import SimpleTestCase, TestCase, override_settings
from django_ses.signals import bounce_received, complaint_received
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog

from core.ses_signals import handle_ses_bounce_received, handle_ses_complaint_received


class SESOperationalVisibilityTests(SimpleTestCase):
    @override_settings(
        AWS_SES_ADD_BOUNCE_TO_BLACKLIST=False,
        AWS_SES_USE_BLACKLIST=False,
    )
    def test_bounce_signal_emits_structured_log_without_raw_email(self) -> None:
        with patch("core.ses_signals.logger.warning") as warning_mock:
            bounce_received.send(
                sender=self.__class__,
                mail_obj={"messageId": "ses-message-123"},
                bounce_obj={
                    "bouncedRecipients": [
                        {"emailAddress": "alice@example.org"},
                    ]
                },
                raw_message=b"{}",
            )

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(extra.get("event"), "astra.email.ses.event_received")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("outcome"), "received")
        self.assertEqual(extra.get("ses_event_type"), "bounce")
        self.assertEqual(extra.get("recipient_domain"), "example.org")
        self.assertEqual(extra.get("event_source"), "django_ses.bounce_received")

        message_id_hash = str(extra.get("ses_message_id_hash") or "")
        self.assertTrue(message_id_hash)
        self.assertNotEqual(message_id_hash, "ses-message-123")

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("alice@example.org", serialized_payload)

    @override_settings(
        AWS_SES_ADD_COMPLAINT_TO_BLACKLIST=False,
        AWS_SES_USE_BLACKLIST=False,
    )
    def test_complaint_signal_emits_structured_log_without_raw_email(self) -> None:
        with patch("core.ses_signals.logger.warning") as warning_mock:
            complaint_received.send(
                sender=self.__class__,
                mail_obj={},
                complaint_obj={
                    "complainedRecipients": [
                        {"emailAddress": "complaint@example.net"},
                    ]
                },
                raw_message=b"{}",
            )

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(extra.get("event"), "astra.email.ses.event_received")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("outcome"), "received")
        self.assertEqual(extra.get("ses_event_type"), "complaint")
        self.assertEqual(extra.get("recipient_domain"), "example.net")
        self.assertEqual(extra.get("event_source"), "django_ses.complaint_received")
        self.assertEqual(extra.get("ses_message_id_present"), False)

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("complaint@example.net", serialized_payload)

    def test_blacklisted_email_admin_is_registered(self) -> None:
        from django_ses.models import BlacklistedEmail

        self.assertIn(BlacklistedEmail, admin.site._registry)
        model_admin = admin.site._registry[BlacklistedEmail]
        self.assertIn("email", tuple(model_admin.search_fields or ()))


class SESPostOfficeAuditLogTests(TestCase):
    def _create_email(self, *, message_id: str, status: int) -> PostOfficeEmail:
        return PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="to@example.com",
            subject="Test",
            message="Test message",
            message_id=message_id,
            status=status,
        )

    def test_bounce_creates_post_office_log_for_matching_smtp_message_id(self) -> None:
        email = self._create_email(message_id="<abc123@example.com>", status=POST_OFFICE_STATUS.queued)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={
                "messageId": "ses-internal-id",
                "commonHeaders": {"messageId": "<abc123@example.com>"},
            },
            bounce_obj={
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        log_entry = (
            PostOfficeLog.objects.filter(email=email, exception_type="SESBounce")
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(log_entry)
        assert log_entry is not None
        self.assertEqual(log_entry.exception_type, "SESBounce")
        self.assertEqual(log_entry.status, POST_OFFICE_STATUS.failed)
        self.assertIn("Permanent", log_entry.message)
        self.assertIn("to@example.com", log_entry.message)

    def test_bounce_marks_queued_email_as_failed(self) -> None:
        email = self._create_email(message_id="<queued@example.com>", status=POST_OFFICE_STATUS.queued)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<queued@example.com>"}},
            bounce_obj={
                "bounceType": "Transient",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.status, POST_OFFICE_STATUS.failed)

    def test_bounce_keeps_sent_email_status_but_still_logs_failure(self) -> None:
        email = self._create_email(message_id="<sent@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<sent@example.com>"}},
            bounce_obj={
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.status, POST_OFFICE_STATUS.sent)
        self.assertTrue(PostOfficeLog.objects.filter(email=email, exception_type="SESBounce").exists())

    def test_bounce_with_missing_or_unmatched_message_id_is_safe(self) -> None:
        self._create_email(message_id="<existing@example.com>", status=POST_OFFICE_STATUS.queued)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<missing@example.com>"}},
            bounce_obj={
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "missing@example.com"}],
            },
            raw_message=b"{}",
        )

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj=None,
            bounce_obj=None,
            raw_message=b"{}",
        )

        self.assertEqual(PostOfficeLog.objects.count(), 0)

    def test_complaint_creates_post_office_log_for_matching_smtp_message_id(self) -> None:
        email = self._create_email(message_id="<complaint@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_complaint_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<complaint@example.com>"}},
            complaint_obj={
                "complainedRecipients": [{"emailAddress": "complaint@example.com"}],
            },
            raw_message=b"{}",
        )

        log_entry = (
            PostOfficeLog.objects.filter(email=email, exception_type="SESComplaint")
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(log_entry)
        assert log_entry is not None
        self.assertEqual(log_entry.exception_type, "SESComplaint")
        self.assertEqual(log_entry.status, POST_OFFICE_STATUS.failed)
        self.assertEqual(log_entry.message, "SES spam complaint received")