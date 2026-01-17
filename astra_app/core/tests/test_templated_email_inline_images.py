from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings


class TemplatedEmailInlineImagesTests(TestCase):
    @override_settings(AWS_STORAGE_BUCKET_NAME="astra-media")
    def test_queue_templated_email_drops_missing_inline_image_in_storage(self):
        """Missing inline images must not block email queuing.

        This catches regressions where `{% inline_image 'http(s)://...' %}`
        raises FileNotFoundError/ValueError and prevents emails from going out.
        """

        from post_office.models import EmailTemplate

        from core.templated_email import queue_templated_email

        EmailTemplate.objects.create(
            name="inline-image-missing-test",
            subject="Reset for {{ username }}",
            html_content=(
                "<img src=\"{% inline_image 'http://localhost:9000/astra-media/mail-images/logo.png' %}\" />\n"
                "<p>{{ reset_url }}</p>"
            ),
            content="Reset: {{ reset_url }}",
        )

        with (
            patch("core.templated_email.default_storage.open", side_effect=FileNotFoundError),
            patch("post_office.mail.send", autospec=True) as send_mock,
        ):
            queue_templated_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                template_name="inline-image-missing-test",
                context={"username": "alice", "reset_url": "http://example.test/reset"},
            )

        self.assertEqual(send_mock.call_count, 1)
        kwargs = send_mock.call_args.kwargs
        self.assertIn("subject", kwargs)
        self.assertIn("message", kwargs)
        self.assertIn("html_message", kwargs)
        self.assertIn("http://example.test/reset", kwargs.get("html_message") or "")
