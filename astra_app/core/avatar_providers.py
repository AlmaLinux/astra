from types import SimpleNamespace

from avatar.templatetags.avatar_tags import avatar_url
from django.core.files.storage import default_storage

from core.avatar_storage import avatar_path_handler
from core.views_utils import try_get_username_from_user


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


def resolve_avatar_urls_for_users(
    users: list[object],
    *,
    width: int,
    height: int,
) -> tuple[dict[str, str], int, int]:
    """Resolve avatar URLs once per unique username for a request-scoped user set.

    Cache keys are case-insensitive because upstream user data can vary by
    letter case while representing the same FreeIPA identity.
    """

    avatar_url_by_username: dict[str, str] = {}
    avatar_url_by_key: dict[str, str] = {}
    avatar_resolution_count = 0
    avatar_fallback_count = 0

    for user in users:
        username = try_get_username_from_user(user)
        if not username:
            continue
        cache_key = username.lower()
        if cache_key in avatar_url_by_key:
            avatar_url_by_username[username] = avatar_url_by_key[cache_key]
            continue

        avatar_resolution_count += 1
        try:
            resolved_avatar_url = str(avatar_url(user, width, height) or "").strip()
        except Exception:
            resolved_avatar_url = ""

        if not resolved_avatar_url:
            avatar_fallback_count += 1
        avatar_url_by_key[cache_key] = resolved_avatar_url
        avatar_url_by_username[username] = resolved_avatar_url

    return avatar_url_by_username, avatar_resolution_count, avatar_fallback_count
