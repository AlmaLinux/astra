import io
from email.message import Message
from unittest.mock import patch

from django.core import mail
from django.core.files.base import ContentFile
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.test import TestCase, override_settings

from core.mail_backends import RelatedInlineImagesMixin
from core.templated_email import queue_composed_email

# Minimal valid 1x1 PNG for MIME type detection in inline-image rendering.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_LOGO_HTML = (
    "{% load post_office %}"
    "<img src=\"{% inline_image 'http://localhost:9000/astra-media/mail-images/logo.png' %}\" /><p>Hi</p>"
)


class _Backend(RelatedInlineImagesMixin, LocmemBackend):
    pass


def _tree(part: Message) -> object:
    if part.is_multipart():
        return (part.get_content_type(), [_tree(child) for child in part.get_payload()])
    return part.get_content_type()


@override_settings(AWS_STORAGE_BUCKET_NAME="astra-media")
class RelatedInlineImagesTests(TestCase):
    def _send(self, *, html_source: str, text_source: str, attachments: dict[str, object] | None = None) -> Message:
        with (
            patch("core.templated_email.default_storage.open", side_effect=lambda *_args: io.BytesIO(_PNG_BYTES)),
            patch("post_office.models.Email.dispatch"),
        ):
            email = queue_composed_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                subject_source="Hello",
                text_source=text_source,
                html_source=html_source,
                context={},
                attachments=attachments,
            )
        _Backend().send_messages([email.email_message()])
        return mail.outbox[-1].message()

    def test_inline_images_are_related_to_the_html_and_other_attachments_stay_attached(self) -> None:
        message = self._send(
            html_source=_LOGO_HTML,
            text_source="Hi",
            attachments={"invoice.pdf": {"file": ContentFile(b"%PDF-1.4"), "mimetype": "application/pdf"}},
        )

        self.assertEqual(
            _tree(message),
            (
                "multipart/mixed",
                [
                    ("multipart/alternative", ["text/plain", ("multipart/related", ["text/html", "image/png"])]),
                    "application/pdf",
                ],
            ),
        )
        image = message.get_payload()[0].get_payload()[1].get_payload()[1]
        self.assertEqual(image.get_payload(decode=True), _PNG_BYTES)
        html = message.get_payload()[0].get_payload()[1].get_payload()[0].get_content()
        self.assertIn(f'src="cid:{image["Content-ID"].strip("<>")}"', html)

    def test_html_only_email_gets_related_images(self) -> None:
        message = self._send(html_source=_LOGO_HTML, text_source="")

        self.assertEqual(_tree(message), ("multipart/mixed", [("multipart/related", ["text/html", "image/png"])]))

    def test_email_without_inline_images_is_unchanged(self) -> None:
        message = self._send(html_source="<p>Hi</p>", text_source="Hi")

        self.assertEqual(_tree(message), ("multipart/alternative", ["text/plain", "text/html"]))
