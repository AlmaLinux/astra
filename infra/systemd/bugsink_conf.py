"""Bugsink Django settings overrides for Astra.

This file is meant to be mounted into the Bugsink container at /app/bugsink_conf.py.

Why this exists:
- Bugsink is a Django app.
- Astra is also a Django app.
- When both are hosted on the same hostname, Django's default cookie names
  (sessionid/csrftoken) can collide and cause login loops.

We avoid that by using Bugsink-specific cookie names and scoping cookies to the
Bugsink subpath.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from bugsink.conf_utils import (
    deduce_allowed_hosts,
    deduce_script_name,
    eat_your_own_dogfood,
    int_or_none,
)
from bugsink.settings.default import *  # noqa: F403
from bugsink.settings.default import DATABASES  # noqa: F401


_KIBIBYTE: int = 1024
_MEBIBYTE: int = 1024 * _KIBIBYTE

_PORT: str = os.environ.get("PORT", "8000")


IS_DOCKER: bool = True

DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
DEBUG_CSRF: bool | str = (
    "USE_DEBUG" if os.getenv("DEBUG_CSRF") == "USE_DEBUG" else os.getenv("DEBUG_CSRF", "False").lower() in ("true", "1", "yes")
)


# The security checks on SECRET_KEY are done as part of:
#   bugsink-manage check --deploy
SECRET_KEY: str = os.getenv("SECRET_KEY", "")


BEHIND_HTTPS_PROXY: bool = os.getenv("BEHIND_HTTPS_PROXY", "False").lower() in ("true", "1", "yes")
BEHIND_PLAIN_HTTP_PROXY: bool = os.getenv("BEHIND_PLAIN_HTTP_PROXY", "False").lower() in ("true", "1", "yes")

if BEHIND_HTTPS_PROXY or BEHIND_PLAIN_HTTP_PROXY:
    # Bugsink's docker template hard-codes this, so your proxy must match.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # noqa: F405
    USE_X_REAL_IP = True  # noqa: F405

if BEHIND_HTTPS_PROXY:
    SESSION_COOKIE_SECURE = True  # noqa: F405
    CSRF_COOKIE_SECURE = True  # noqa: F405
else:
    # Mirrors Bugsink's docker template behavior: avoid security warnings for
    # local HTTP setups.
    SILENCED_SYSTEM_CHECKS += [  # noqa: F405
        "security.W012",  # SESSION_COOKIE_SECURE
        "security.W016",  # CSRF_COOKIE_SECURE
    ]


TIME_ZONE: str = os.getenv("TIME_ZONE", "UTC")


SENTRY_DSN: str | None = os.getenv("SENTRY_DSN")
# "Dogfood" Bugsink by letting it report its own internal errors.
eat_your_own_dogfood(SENTRY_DSN)


SNAPPEA = {
    # Docker image runs snappea; keep this consistent with upstream.
    "TASK_ALWAYS_EAGER": False,
    "WORKAHOLIC": True,

    "NUM_WORKERS": int(os.getenv("SNAPPEA_NUM_WORKERS", 2)),
    "STATS_RETENTION_MINUTES": int(os.getenv("SNAPPEA_STATS_RETENTION_MINUTES", 60 * 24 * 7)),

    "PID_FILE": None,
}


# Not actually a "database": this is a (tmp to the container) message queue.
DATABASES["snappea"]["NAME"] = "/tmp/snappea.sqlite3"


if os.getenv("DATABASE_URL"):
    database_url: str = os.environ["DATABASE_URL"]
    parsed = urlparse(database_url)

    if parsed.scheme == "mysql":
        DATABASES["default"] = {
            "ENGINE": "django.db.backends.mysql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username,
            "PASSWORD": parsed.password,
            "HOST": parsed.hostname,
            "PORT": parsed.port or "3306",
        }
    elif parsed.scheme in {"postgres", "postgresql"}:
        DATABASES["default"] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username,
            "PASSWORD": parsed.password,
            "HOST": parsed.hostname,
            "PORT": parsed.port or "5432",
        }
    else:
        raise ValueError(f"For DATABASE_URL, only mysql and postgres are supported, not {parsed.scheme!r}.")
else:
    # sqlite fallback: useful for throwaway setups.
    DATABASES["default"]["NAME"] = os.getenv("DATABASE_PATH", "/data/db.sqlite3")


if os.getenv("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # noqa: F405
    EMAIL_HOST = os.getenv("EMAIL_HOST")  # noqa: F405
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")  # noqa: F405
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")  # noqa: F405
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))  # noqa: F405

    email_use_ssl = os.getenv("EMAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
    EMAIL_USE_SSL = email_use_ssl  # noqa: F405
    EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", str(not email_use_ssl)).lower() in ("true", "1", "yes")  # noqa: F405

    if os.getenv("EMAIL_LOGGING", "false").lower() in ("true", "1", "yes"):
        LOGGING["loggers"]["bugsink.email"]["level"] = "INFO"  # noqa: F405
else:
    EMAIL_BACKEND = "bugsink.email_backends.QuietConsoleEmailBackend"  # noqa: F405

if os.getenv("EMAIL_TIMEOUT"):
    EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT"))  # noqa: F405

SERVER_EMAIL = DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Bugsink <bugsink@example.org>")  # noqa: F405


CB_ANYBODY = "CB_ANYBODY"
CB_MEMBERS = "CB_MEMBERS"
CB_ADMINS = "CB_ADMINS"
CB_NOBODY = "CB_NOBODY"


BUGSINK = {
    # The URL where the Bugsink instance is hosted. This is used in email
    # notifications and to construct DSNs.
    "BASE_URL": os.getenv("BASE_URL", f"http://localhost:{_PORT}"),  # no trailing slash
    "SITE_TITLE": os.getenv("SITE_TITLE", "Bugsink"),

    # Who can register users.
    "SINGLE_USER": os.getenv("SINGLE_USER", "False").lower() in ("true", "1", "yes"),
    "USER_REGISTRATION": os.getenv("USER_REGISTRATION", CB_MEMBERS),
    "USER_REGISTRATION_VERIFY_EMAIL": os.getenv("USER_REGISTRATION_VERIFY_EMAIL", "True").lower() in ("true", "1", "yes"),
    "USER_REGISTRATION_VERIFY_EMAIL_EXPIRY": int(os.getenv("USER_REGISTRATION_VERIFY_EMAIL_EXPIRY", 7 * 24 * 60 * 60)),

    # Teams.
    "SINGLE_TEAM": os.getenv("SINGLE_TEAM", "False").lower() in ("true", "1", "yes"),
    "TEAM_CREATION": os.getenv("TEAM_CREATION", CB_MEMBERS),

    # Limits mirroring Sentry Relay.
    "MAX_EVENT_SIZE": int(os.getenv("MAX_EVENT_SIZE", _MEBIBYTE)),
    "MAX_EVENT_COMPRESSED_SIZE": int(os.getenv("MAX_EVENT_COMPRESSED_SIZE", 200 * _KIBIBYTE)),
    "MAX_ENVELOPE_SIZE": int(os.getenv("MAX_ENVELOPE_SIZE", 100 * _MEBIBYTE)),
    "MAX_ENVELOPE_COMPRESSED_SIZE": int(os.getenv("MAX_ENVELOPE_COMPRESSED_SIZE", 20 * _MEBIBYTE)),

    # Retention / rate-limits.
    "MAX_EVENTS_PER_PROJECT_PER_5_MINUTES": int(os.getenv("MAX_EVENTS_PER_PROJECT_PER_5_MINUTES", 1_000)),
    "MAX_EVENTS_PER_PROJECT_PER_HOUR": int(os.getenv("MAX_EVENTS_PER_PROJECT_PER_HOUR", 5_000)),
    "MAX_EVENTS_PER_PROJECT_PER_MONTH": int(os.getenv("MAX_EVENTS_PER_PROJECT_PER_MONTH", 1_000_000)),
    "MAX_EVENTS_PER_5_MINUTES": int(os.getenv("MAX_EVENTS_PER_5_MINUTES", 1_000)),
    "MAX_EVENTS_PER_HOUR": int(os.getenv("MAX_EVENTS_PER_HOUR", 5_000)),
    "MAX_EVENTS_PER_MONTH": int(os.getenv("MAX_EVENTS_PER_MONTH", 1_000_000)),

    "MAX_RETENTION_PER_PROJECT_EVENT_COUNT": int_or_none(os.getenv("MAX_RETENTION_PER_PROJECT_EVENT_COUNT")),
    "MAX_RETENTION_EVENT_COUNT": int_or_none(os.getenv("MAX_RETENTION_EVENT_COUNT")),

    # Debugging.
    "VALIDATE_ON_DIGEST": os.getenv("VALIDATE_ON_DIGEST", "none").lower(),
    "KEEP_ENVELOPES": int(os.getenv("KEEP_ENVELOPES", 0)),
    "API_LOG_UNIMPLEMENTED_CALLS": os.getenv("API_LOG_UNIMPLEMENTED_CALLS", "false").lower() in ("true", "1", "yes"),
    "KEEP_ARTIFACT_BUNDLES": os.getenv("KEEP_ARTIFACT_BUNDLES", "false").lower() in ("true", "1", "yes"),

    "MINIMIZE_INFORMATION_EXPOSURE": os.getenv("MINIMIZE_INFORMATION_EXPOSURE", "false").lower() in ("true", "1", "yes"),
    "PHONEHOME": os.getenv("PHONEHOME", "true").lower() in ("true", "1", "yes"),

    "FEATURE_MINIDUMPS": os.getenv("FEATURE_MINIDUMPS", "false").lower() in ("true", "1", "yes"),
}


if os.getenv("ALLOWED_HOSTS"):
    ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS").split(",")  # noqa: F405
else:
    ALLOWED_HOSTS = deduce_allowed_hosts(BUGSINK["BASE_URL"])  # noqa: F405


FORCE_SCRIPT_NAME = deduce_script_name(BUGSINK["BASE_URL"])  # noqa: F405
if FORCE_SCRIPT_NAME:
    STATIC_URL = f"{FORCE_SCRIPT_NAME}/static/"  # noqa: F405


# Cookie isolation: avoid collisions with Astra's Django cookies.
#
# Also scope cookies to the Bugsink subpath so browsers do not send them to other
# apps on the same hostname.
_bugs_path = FORCE_SCRIPT_NAME or "/"
SESSION_COOKIE_NAME = os.getenv("BUGSINK_SESSION_COOKIE_NAME", "bugsink_sessionid")  # noqa: F405
CSRF_COOKIE_NAME = os.getenv("BUGSINK_CSRF_COOKIE_NAME", "bugsink_csrftoken")  # noqa: F405
SESSION_COOKIE_PATH = os.getenv("BUGSINK_SESSION_COOKIE_PATH", _bugs_path)  # noqa: F405
CSRF_COOKIE_PATH = os.getenv("BUGSINK_CSRF_COOKIE_PATH", _bugs_path)  # noqa: F405
