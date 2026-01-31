from __future__ import annotations

from types import SimpleNamespace

from django.core.files.storage import default_storage

from core.avatar_storage import avatar_path_handler


class LocalS3AvatarProvider:
    """Serve locally uploaded avatars from object storage.

    This is intentionally not DB-backed: the avatar's existence is derived from
    whether the object exists at the deterministic storage key.
    """

    @staticmethod
    def get_avatar_url(user: object, width: int, height: int) -> str:
        # Store as a stable PNG key regardless of the uploaded original format.
        key = avatar_path_handler(instance=SimpleNamespace(user=user), ext="png")
        if not default_storage.exists(key):
            return ""
        return str(default_storage.url(key) or "").strip()
