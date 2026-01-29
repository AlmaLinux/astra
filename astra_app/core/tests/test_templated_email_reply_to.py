from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from post_office.models import EmailTemplate

from core.templated_email import queue_templated_email


class QueueTemplatedEmailReplyToTests(TestCase):
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
