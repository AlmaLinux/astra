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
            patch("post_office.models.Email.dispatch"),
        ):
            email = queue_templated_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                template_name="inline-image-missing-test",
                context={"username": "alice", "reset_url": "http://example.test/reset"},
            )

        self.assertIsNotNone(email.pk)
        self.assertIn("http://example.test/reset", email.html_message)

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
    def test_bulk_saved_emails_carry_their_inline_images(self) -> None:
        """Bulk runs build emails with commit=False; their inline images must still be sent,
        stored once and shared by every email in the run."""
        from post_office.models import Attachment

        from core.templated_email import bulk_save_emails, queue_composed_email

        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        html = "{% load post_office %}<img src=\"{% inline_image 'http://localhost:9000/astra-media/mail-images/logo.png' %}\" />"

        with (
            patch("core.templated_email.default_storage.open", side_effect=lambda *_args: io.BytesIO(png_bytes)),
            patch("post_office.models.Email.dispatch"),
        ):
            emails = [
                queue_composed_email(
                    recipients=[address],
                    sender="noreply@example.com",
                    subject_source="Hello",
                    text_source="Hi",
                    html_source=html,
                    context={},
                    commit=False,
                )
                for address in ["alice@example.com", "bob@example.com"]
            ]
            bulk_save_emails(emails)

        self.assertEqual(Attachment.objects.count(), 1)
        for email in emails:
            image_parts = [
                part for part in email.email_message().message().walk() if part.get_content_maintype() == "image"
            ]
            self.assertEqual(len(image_parts), 1)
            self.assertEqual(image_parts[0].get_payload(decode=True), png_bytes)

    @override_settings(AWS_STORAGE_BUCKET_NAME="astra-media")
    def test_delivered_inline_image_decodes_to_original_bytes(self) -> None:
        """post_office copies our attachment headers verbatim and never encodes the
        payload itself, so the stored bytes must match the declared base64 encoding."""
        from core.templated_email import queue_composed_email

        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        html = "{% load post_office %}<img src=\"{% inline_image 'http://localhost:9000/astra-media/mail-images/logo.png' %}\" />"

        with (
            patch("core.templated_email.default_storage.open", return_value=io.BytesIO(png_bytes)),
            patch("post_office.models.Email.dispatch"),
        ):
            email = queue_composed_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                subject_source="Hello",
                text_source="Hi",
                html_source=html,
                context={},
            )

        image_parts = [part for part in email.email_message().message().walk() if part.get_content_maintype() == "image"]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0].get_payload(decode=True), png_bytes)
        self.assertIn(f'src="cid:{image_parts[0]["Content-ID"].strip("<>")}"', email.html_message)
