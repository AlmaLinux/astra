from __future__ import annotations

import hashlib
import hmac
import os

from django.conf import settings
from django.utils.encoding import force_bytes

from core.views_utils import _normalize_str


def avatar_path_handler(
    instance: object | None = None,
    filename: str | None = None,
    width: int | None = None,
    height: int | None = None,
    ext: str | None = None,
) -> str:
    """Return a deterministic, non-enumerable object key for a user's avatar.

    We intentionally do not store avatars under a username-based key. Instead we
    derive the filename from HMAC(SECRET_KEY, normalized_username).

    `instance` is intentionally typed as `object | None` because callers may be
    third-party models (e.g. django-avatar). In those cases, direct attribute
    access is not guaranteed safe.
    """

    username = ""
    if instance is not None:
        # `instance` may be from a third-party model; `user` and `get_username`
        # may not exist.
        user = getattr(instance, "user", None)
        if user is not None:
            get_username = getattr(user, "get_username", None)
            if callable(get_username):
                username = str(get_username() or "")
            else:
                username = str(getattr(user, "username", "") or "")

    normalized = _normalize_str(username)
    digest = hmac.new(
        force_bytes(settings.SECRET_KEY),
        force_bytes(normalized),
        hashlib.sha256,
    ).hexdigest()

    resolved_ext = str(ext or "").strip().lower()
    if not resolved_ext and filename:
        _, file_ext = os.path.splitext(str(filename))
        resolved_ext = file_ext.lstrip(".").lower()

    basename = digest
    if resolved_ext:
        basename = f"{basename}.{resolved_ext}"

    base_dir = str(settings.AVATAR_STORAGE_DIR or "avatars").strip("/")
    parts = [base_dir]
    if width or height:
        parts.extend(["resized", str(width or ""), str(height or "")])
    parts.append(os.path.basename(basename))
    return os.path.join(*parts)
