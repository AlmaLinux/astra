from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import SimpleTestCase

from core.avatar_storage import avatar_path_handler
from core.mail_attachments import MAIL_ATTACHMENTS_UPLOAD_DIR
from core.models import Organization
from core.sponsorship_invoices import invoice_storage_key


class ObjectCacheControlTests(SimpleTestCase):
    """Objects overwritten in place under the same URL must make the CDN revalidate."""

    def _upload_extra_args(self, key: str) -> dict[str, str]:
        # Build the configured default backend fresh, so the test covers the STORAGES wiring.
        storage = storages.create_storage(settings.STORAGES["default"])
        with patch.object(type(storage), "bucket", new_callable=PropertyMock) as bucket:
            storage.save(key, ContentFile(b"content"))
        return bucket.return_value.Object.return_value.upload_fileobj.call_args.kwargs["ExtraArgs"]

    def test_invoice_uploads_require_revalidation(self) -> None:
        key = invoice_storage_key(Organization(invoice_token="token"), "0001-2026-123456")
        self.assertEqual(self._upload_extra_args(key)["CacheControl"], "no-cache")

    def test_avatar_uploads_require_revalidation(self) -> None:
        key = avatar_path_handler(instance=SimpleNamespace(user=SimpleNamespace(username="alice")), ext="png")
        self.assertEqual(self._upload_extra_args(key)["CacheControl"], "no-cache")

    def test_uploads_under_unique_keys_stay_cacheable(self) -> None:
        extra_args = self._upload_extra_args(f"{MAIL_ATTACHMENTS_UPLOAD_DIR}/token/report.pdf")
        self.assertNotIn("CacheControl", extra_args)
