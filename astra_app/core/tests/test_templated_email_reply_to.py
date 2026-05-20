
from email.header import decode_header
from unittest.mock import patch

from django.core.mail import EmailMultiAlternatives
from django.test import TestCase
from post_office.models import EmailTemplate

from core.templated_email import queue_templated_email


class QueueTemplatedEmailReplyToTests(TestCase):
    def test_subject_preserves_literal_apostrophes_and_unicode(self) -> None:
        EmailTemplate.objects.create(
            name="subject-encoding-test",
            subject="{{ invitation_subject }}",
            content="Text",
            html_content="",
        )

        class _DummyEmail:
            template = None
            context = None

            def save(self, update_fields=None) -> None:
                return None

        with patch("core.templated_email.post_office.mail.send", return_value=_DummyEmail()) as send_mock:
            queue_templated_email(
                recipients=["alice@example.com"],
                sender="from@example.com",
                template_name="subject-encoding-test",
                context={
                    "invitation_subject": "Who's ready for cafe? ☕",
                },
            )

        _args, kwargs = send_mock.call_args
        subject = kwargs.get("subject")
        self.assertEqual(subject, "Who's ready for cafe? ☕")
        self.assertNotIn("&#x27;", subject)

        encoded_message = EmailMultiAlternatives(
            subject=subject,
            body="Text",
            from_email="from@example.com",
            to=["alice@example.com"],
        ).message()
        decoded_subject = "".join(
            part.decode(charset or "ascii") if isinstance(part, bytes) else part
            for part, charset in decode_header(encoded_message["Subject"])
        )
        self.assertEqual(decoded_subject, subject)

    def test_reply_to_is_sent_as_header(self) -> None:
        EmailTemplate.objects.create(
            name="reply-to-test",
            subject="Hello",
            content="Text",
            html_content="",
        )

        class _DummyEmail:
            template = None
            context = None

            def save(self, update_fields=None) -> None:
                return None

        with patch("core.templated_email.post_office.mail.send", return_value=_DummyEmail()) as send_mock:
            queue_templated_email(
                recipients=["alice@example.com"],
                sender="from@example.com",
                template_name="reply-to-test",
                context={"username": "alice"},
                reply_to=["reply@example.com"],
            )

        _args, kwargs = send_mock.call_args
        self.assertEqual(kwargs.get("headers"), {"Reply-To": "reply@example.com"})
