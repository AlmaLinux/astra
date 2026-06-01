import hashlib

from django.core.cache import cache


def _normalized_key_parts(scope: str, key_parts: list[str]) -> list[str]:
    return [scope, *[str(part).strip().lower() for part in key_parts if str(part).strip()]]


def _rate_limit_cache_key(scope: str, key_parts: list[str]) -> str:
    material = "|".join(_normalized_key_parts(scope, key_parts)).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return f"astra:rl:{scope}:{digest}"


def _rate_limit_subject_index_key(scope: str, subject: str) -> str:
    normalized_subject = str(subject).strip().lower()
    digest = hashlib.sha256(normalized_subject.encode("utf-8")).hexdigest()
    return f"astra:rl-index:{scope}:{digest}"


def _remember_rate_limit_subject_key(*, scope: str, subject: str, cache_key: str, window_seconds: int) -> None:
    if not subject:
        return

    index_key = _rate_limit_subject_index_key(scope, subject)
    existing_keys = cache.get(index_key)
    remembered_keys = {str(value) for value in existing_keys if str(value).strip()} if isinstance(existing_keys, list) else set()
    remembered_keys.add(cache_key)
    cache.set(index_key, sorted(remembered_keys), timeout=window_seconds)


def clear_subject_rate_limit(*, scope: str, subject: str) -> None:
    index_key = _rate_limit_subject_index_key(scope, subject)
    existing_keys = cache.get(index_key)
    if isinstance(existing_keys, list):
        for cache_key in existing_keys:
            cache.delete(str(cache_key))
    cache.delete(index_key)


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

    cache_key = _rate_limit_cache_key(scope, key_parts)
    subject = str(key_parts[-1]).strip().lower() if key_parts else ""
    _remember_rate_limit_subject_key(
        scope=scope,
        subject=subject,
        cache_key=cache_key,
        window_seconds=window_seconds,
    )

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
