import io
from unittest.mock import patch

from django.test import TestCase, override_settings


class TemplatedEmailInlineImagesTests(TestCase):
    @override_settings(
        AWS_STORAGE_BUCKET_NAME="almalinux-astra",
        AWS_S3_CUSTOM_DOMAIN="static.astra.almalinux.org",
    )
    def test_storage_key_accepts_custom_domain_without_bucket(self) -> None:
        from core.templated_email import _storage_key_from_inline_image_arg

        key = _storage_key_from_inline_image_arg(
            "https://static.astra.almalinux.org/mail-images/logo.svg"
        )

        self.assertEqual(key, "mail-images/logo.svg")

    @override_settings(AWS_STORAGE_BUCKET_NAME="astra-media")
    def test_queue_templated_email_drops_missing_inline_image_in_storage(self) -> None:
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
            patch("core.templated_email.logger.warning") as warning_mock,
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

        warning_mock.assert_called_once()
        _warning_args, warning_kwargs = warning_mock.call_args
        extra = warning_kwargs.get("extra") or {}

        self.assertEqual(extra.get("event"), "astra.email.inline_image.missing")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("outcome"), "warning")
        self.assertEqual(extra.get("template_name"), "inline-image-missing-test")
        self.assertEqual(
            extra.get("image_ref"),
            "http://localhost:9000/astra-media/mail-images/logo.png",
        )
        self.assertEqual(extra.get("correlation_id"), "email.templated.queue")

    @override_settings(AWS_STORAGE_BUCKET_NAME="astra-media")
    def test_queue_composed_email_preserves_inline_image_attachment_content_id(self) -> None:
        from core.templated_email import queue_composed_email

        html = "{% load post_office %}<img src=\"{% inline_image 'http://localhost:9000/astra-media/mail-images/logo.png' %}\" />"

        # Minimal valid 1x1 PNG for MIME type detection in inline-image rendering.
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        class _DummyEmail:
            id = 123

        with (
            patch("core.templated_email.default_storage.open", return_value=io.BytesIO(png_bytes)),
            patch("core.templated_email.post_office.mail.send", return_value=_DummyEmail()) as send_mock,
        ):
            queue_composed_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                subject_source="Hello {{ username }}",
                text_source="Hi {{ username }}",
                html_source=html,
                context={"username": "alice"},
            )

        _args, kwargs = send_mock.call_args
        self.assertEqual(kwargs.get("render_on_delivery"), False)
        attachments = kwargs.get("attachments") or {}
        self.assertTrue(attachments)
        has_content_id = any(
            isinstance(meta, dict)
            and isinstance(meta.get("headers"), dict)
            and bool(meta["headers"].get("Content-ID"))
            for meta in attachments.values()
        )
        self.assertTrue(has_content_id)
