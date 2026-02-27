import hashlib

from django.core.cache import cache


def allow_request(
    *,
    scope: str,
    key_parts: list[str],
    limit: int,
    window_seconds: int,
) -> bool:
    """Return True if the request is allowed under the configured rate limit.

    This uses Django's cache as a shared counter. Keys are hashed to avoid
    cache backend key-length limitations.

    - `scope` identifies the endpoint/operation.
    - `key_parts` should include stable identity elements (e.g. election id, username, IP).
    """

    if limit <= 0 or window_seconds <= 0:
        return True

    normalized_parts = [scope, *[str(p).strip().lower() for p in key_parts if str(p).strip()]]
    material = "|".join(normalized_parts).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    cache_key = f"astra:rl:{scope}:{digest}"

    if cache.add(cache_key, 1, timeout=window_seconds):
        return True

    try:
        count = int(cache.incr(cache_key))
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        return True

    ttl_updated = False
    try:
        ttl_updated = bool(cache.touch(cache_key, timeout=window_seconds))
    except Exception:
        ttl_updated = False

    if not ttl_updated:
        cache.set(cache_key, count, timeout=window_seconds)

    return count <= limit
