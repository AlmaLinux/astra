from unittest.mock import patch

from django.contrib import admin
from django.test import SimpleTestCase, override_settings
from django_ses.signals import bounce_received, complaint_received


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