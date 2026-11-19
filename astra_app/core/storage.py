from typing import override

from django.conf import settings
from storages.backends.s3 import S3Storage

INVOICE_STORAGE_DIR = "invoices"


class AstraS3Storage(S3Storage):
    """S3 storage that makes caches revalidate objects Astra overwrites in place.

    Invoices and avatars are rewritten under the same key so links already sent or
    rendered keep working. Without a Cache-Control header, Cloudflare (and browsers,
    through the TTL Cloudflare adds) keep serving the old copy after a rewrite.
    """

    @override
    def get_object_parameters(self, name: str) -> dict[str, str]:
        params = super().get_object_parameters(name)
        if name.startswith((f"{INVOICE_STORAGE_DIR}/", f"{settings.AVATAR_STORAGE_DIR}/")):
            params["CacheControl"] = "no-cache"
        return params
