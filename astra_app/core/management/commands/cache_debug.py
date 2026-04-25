import json
import logging

from django.core.cache import cache, caches
from django.core.management.base import BaseCommand

from core.cache_tools import list_cache_keys_from_backend, safe_cache_preview

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Inspect Django cache entries (handy for FreeIPA cache debugging)."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="List known FreeIPA cache keys.")
        parser.add_argument(
            "--keys",
            action="store_true",
            help="List all cache keys if supported by backend (LocMemCache only).",
        )
        parser.add_argument(
            "--get",
            dest="get_keys",
            action="append",
            help="Fetch a cache entry by key (repeatable).",
        )
        parser.add_argument(
            "--delete",
            dest="delete_keys",
            action="append",
            help="Delete a cache entry by key (repeatable).",
        )
        parser.add_argument(
            "--max-chars",
            type=int,
            default=4000,
            help="Truncate printed values to this many characters (0 = no truncation).",
        )
        parser.add_argument("--pretty", action="store_true", help="Pretty-print dict/list values as JSON.")

    def handle(self, *args, **options):
        max_chars: int = options["max_chars"]
        pretty: bool = options["pretty"]

        delete_keys = options.get("delete_keys") or []
        for key in delete_keys:
            cache.delete(key)
            logger.info("deleted: %s", key)

        get_keys = options.get("get_keys") or []
        for key in get_keys:
            val = cache.get(key)
            if pretty and isinstance(val, (dict, list, tuple)):
                text = json.dumps(val, indent=2, sort_keys=True, default=str)
                if max_chars > 0 and len(text) > max_chars:
                    text = text[:max_chars] + "…"
            else:
                preview = safe_cache_preview(val, max_chars=max_chars)
                text = "<missing>" if preview is None else str(preview)
            logger.info("%s = %s", key, text)

        if options.get("list"):
            # These are the keys used by core/backends.py.
            logger.info("Known FreeIPA keys:")
            logger.info("- freeipa_users_all")
            logger.info("- freeipa_groups_all")
            logger.info("- freeipa_user_<username>")
            logger.info("- freeipa_group_<cn>")

        if options.get("keys"):
            keys = list_cache_keys_from_backend(caches["default"])
            if keys is None:
                logger.info(
                    "This cache backend does not expose keys (try LocMemCache or switch to Redis for inspectability)."
                )
            elif not keys:
                backend = caches["default"]
                backend_path = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
                logger.info("<no keys>")
                logger.info("cache backend: %s", backend_path)
                logger.info(
                    "Note: LocMemCache is per-process. Running `manage.py` in `podman-compose exec` starts a new Python process,"
                    " so it will NOT see the runserver/gunicorn process's in-memory cache."
                )
                logger.info(
                    "If you need to inspect live keys, use a shared cache backend (e.g., Redis) or add an in-app debug view."
                )
            else:
                for k in keys:
                    logger.info(k)

        # Default behavior if no flags: show a tiny hint.
        if not (delete_keys or get_keys or options.get("list") or options.get("keys")):
            logger.info("Use --list, --keys, --get <key>, or --delete <key>.")
