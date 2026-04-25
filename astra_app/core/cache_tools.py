import json
import re
from typing import Any

from django.core.cache import cache, caches
from django.db import connections

_CACHE_VERSION_PREFIX_RE = re.compile(r"^:\d+:")


def _display_cache_key(raw_key: object) -> str:
    text = str(raw_key)
    return _CACHE_VERSION_PREFIX_RE.sub("", text, count=1)


def safe_cache_preview(value: Any, *, max_chars: int) -> Any:
    if value is None:
        return None

    try:
        if isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, sort_keys=True, default=str)
        else:
            text = str(value)
    except Exception:
        text = repr(value)

    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def list_cache_keys_from_backend(backend: object) -> list[str] | None:
    # LocMemCache exposes a per-process dict called _cache.
    if not hasattr(backend, "_cache"):
        if not hasattr(backend, "_table"):
            return None

        table_name = backend._table
        database_alias = "default"
        if hasattr(backend, "_db") and isinstance(backend._db, str) and backend._db:
            database_alias = backend._db
        if not isinstance(table_name, str) or not table_name:
            return None

        connection = connections[database_alias]
        quoted_table_name = connection.ops.quote_name(table_name)
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT cache_key FROM {quoted_table_name} ORDER BY cache_key ASC")
            rows = cursor.fetchall()
        return [_display_cache_key(row[0]) for row in rows]

    internal = backend._cache
    if not isinstance(internal, dict):
        return None
    return sorted(_display_cache_key(k) for k in internal.keys())


def inspect_default_cache(
    *,
    prefix: str | None = None,
    key: str | None = None,
    max_chars: int = 4000,
) -> dict[str, object]:
    backend = caches["default"]
    backend_path = f"{backend.__class__.__module__}.{backend.__class__.__name__}"

    keys = list_cache_keys_from_backend(backend)
    supports_key_listing = keys is not None
    visible_keys = list(keys or [])
    if prefix:
        visible_keys = [cache_key for cache_key in visible_keys if cache_key.startswith(prefix)]

    payload: dict[str, object] = {
        "backend": backend_path,
        "supports_key_listing": supports_key_listing,
        "count": len(visible_keys),
        "keys": visible_keys,
        "known_freeipa_keys": [
            "freeipa_users_all",
            "freeipa_groups_all",
            "freeipa_user_<username>",
            "freeipa_group_<cn>",
        ],
    }

    if key:
        payload["key"] = key
        payload["value_preview"] = safe_cache_preview(backend.get(key), max_chars=max_chars)

    return payload


def clear_default_cache() -> dict[str, object]:
    backend = caches["default"]
    backend_path = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
    cache.clear()
    return {"backend": backend_path, "cleared": True}