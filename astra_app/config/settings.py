import datetime
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_str(name: str, *, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    return value


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean env var, got {raw!r}.")


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError as e:
        raise ImproperlyConfigured(f"{name} must be an integer env var, got {raw!r}.") from e


def _env_list(name: str, *, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return []
    # Match common 12-factor practice: comma-separated lists.
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_database_url(database_url: str) -> dict[str, Any]:
    parts = urlsplit(database_url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            f"Unsupported DATABASE_URL scheme {parts.scheme!r}; expected postgres/postgresql."
        )
    if not parts.hostname:
        raise ImproperlyConfigured("DATABASE_URL must include a hostname.")

    name = (parts.path or "").lstrip("/")
    if not name:
        raise ImproperlyConfigured("DATABASE_URL must include a database name.")

    config: dict[str, Any] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(name),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname,
        "PORT": str(parts.port) if parts.port is not None else "",
    }

    # Pass through simple query parameters (e.g. sslmode=require).
    query = parse_qs(parts.query or "", keep_blank_values=True)
    if query:
        config["OPTIONS"] = {key: values[-1] for key, values in query.items()}

    return config


def _parse_email_url(email_url: str) -> dict[str, Any]:
    parts = urlsplit(email_url)
    if parts.scheme not in {"smtp", "smtps"}:
        raise ImproperlyConfigured(
            f"Unsupported EMAIL_URL scheme {parts.scheme!r}; expected smtp/smtps."
        )
    if not parts.hostname:
        raise ImproperlyConfigured("EMAIL_URL must include a hostname.")

    use_ssl = parts.scheme == "smtps"
    port = parts.port
    if port is None:
        port = 465 if use_ssl else 25

    return {
        "EMAIL_HOST": parts.hostname,
        "EMAIL_PORT": port,
        "EMAIL_HOST_USER": unquote(parts.username or ""),
        "EMAIL_HOST_PASSWORD": unquote(parts.password or ""),
        "EMAIL_USE_SSL": use_ssl,
        "EMAIL_USE_TLS": False,
    }

DEBUG = _env_bool("DEBUG", default=False)

# Self-service Address settings: where to store the ISO 3166-1 country code.
# Some FreeIPA dev schemas don't allow writing to the LDAP "c" attribute.
# Configure in devel via SELF_SERVICE_ADDRESS_COUNTRY_ATTR=fasstatusnote
SELF_SERVICE_ADDRESS_COUNTRY_ATTR = (
    _env_str("SELF_SERVICE_ADDRESS_COUNTRY_ATTR", default="c")
    or "c"
)

# FreeIPA FAS agreement CN used for the Community Code of Conduct.
COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN = "AlmaLinux Community Code of Conduct"

# Django management commands (e.g. `migrate`) still import settings, but they don't
# need certain web-runtime-only secrets. This makes one-off tasks safer and easier
# to run without granting access to unrelated secrets.
_DJANGO_SUBCOMMAND = sys.argv[1] if len(sys.argv) > 1 else ""
_ALLOW_MISSING_RUNTIME_SECRETS = _DJANGO_SUBCOMMAND in {
    "migrate",
    "makemigrations",
    "showmigrations",
    "sqlmigrate",
    "collectstatic",
}

_DEFAULT_SECRET_KEY_PLACEHOLDER = "django-insecure-dev-only-change-me"
SECRET_KEY = _env_str("SECRET_KEY", default=_DEFAULT_SECRET_KEY_PLACEHOLDER) or _DEFAULT_SECRET_KEY_PLACEHOLDER
if not DEBUG and not _ALLOW_MISSING_RUNTIME_SECRETS:
    if "SECRET_KEY" not in os.environ:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set in production (SECRET_KEY env var is missing)."
        )
    # Avoid the default placeholder and overly-short values.
    if SECRET_KEY == _DEFAULT_SECRET_KEY_PLACEHOLDER:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set in production (value is the insecure placeholder)."
        )
    if len(SECRET_KEY) <= 32:
        raise ImproperlyConfigured(
            "SECRET_KEY value is too weak."
        )

_dev_allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]
ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", default=_dev_allowed_hosts if DEBUG else [])
if not DEBUG and not _ALLOW_MISSING_RUNTIME_SECRETS and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'import_export',
    'storages',
    'logentry_admin',
    'post_office',
    'django_ses',
    'avatar',
    'core',
]

# Disable migrations for Django's built-in 'auth' app to silence
# warnings caused by unmanaged models that share the 'auth' label. Enable via
# env var DISABLE_AUTH_MIGRATIONS=1 in container/env when running without a DB.
if os.environ.get('DISABLE_AUTH_MIGRATIONS') == '1':
    MIGRATION_MODULES = {
        'auth': None,
    }

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.FreeIPAServiceClientReuseMiddleware',
    'core.middleware.FreeIPAAuthenticationMiddleware',
    'core.middleware.LoginRequiredMiddleware',
    'core.middleware_admin_log.AdminShadowUserLogEntryMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.membership_review',
                'core.context_processors.organization_nav',
                'core.context_processors.chat_networks',
                'core.context_processors.build_info',
            ],
        },
    },
    {
        # django-post-office inline images require the PostOfficeTemplates engine.
        # See: https://github.com/ui/django-post_office#inlined-images
        'NAME': 'post_office',
        'BACKEND': 'post_office.template.backends.post_office.PostOfficeTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

_database_url = _env_str("DATABASE_URL", default=None)
if not _database_url:
    # In containerized environments (ECS), it's common to inject the database password
    # as a dedicated secret env var rather than embedding it in DATABASE_URL.
    db_host = _env_str("DATABASE_HOST", default=None)
    if db_host:
        from urllib.parse import quote

        db_port = _env_str("DATABASE_PORT", default="5432") or "5432"
        db_name = _env_str("DATABASE_NAME", default="") or ""
        db_user = _env_str("DATABASE_USER", default="") or ""
        db_password = _env_str("DATABASE_PASSWORD", default="") or ""
        if not db_name or not db_user:
            raise ImproperlyConfigured(
                "DATABASE_NAME and DATABASE_USER must be set when using DATABASE_HOST/DATABASE_PASSWORD."
            )

        user_enc = quote(db_user, safe="")
        pass_enc = quote(db_password, safe="")
        name_enc = quote(db_name, safe="")
        _database_url = f"postgres://{user_enc}:{pass_enc}@{db_host}:{db_port}/{name_enc}"

DATABASES = {
    "default": _parse_database_url(
        (_database_url or "postgres://postgres:postgres@db:5432/almalinux_members").strip()
    )
}

JAZZMIN_SETTINGS = {
    "site_title": "AlmaLinux Astra",
    "custom_css": "core/css/admin.css",
}

# Email
# In DEBUG, docker-compose provides EMAIL_URL pointing to Mailhog.
_email_url_raw = os.environ.get("EMAIL_URL", "").strip()
EMAIL_CONFIG = _parse_email_url(_email_url_raw) if _email_url_raw else None
if EMAIL_CONFIG:
    globals().update(EMAIL_CONFIG)

DEFAULT_FROM_EMAIL = _env_str("DEFAULT_FROM_EMAIL", default="webmaster@localhost") or "webmaster@localhost"

# Public base URL used for absolute links in email (cron jobs don't have a request
# context). Example: https://accounts.almalinux.org
PUBLIC_BASE_URL = _env_str("PUBLIC_BASE_URL", default="http://localhost:8000") or "http://localhost:8000"

# Elections
ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS = _env_int(
    "ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS",
    default=0 if DEBUG else 90,
)

ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS = _env_int(
    "ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS",
    default=30,
)

ELECTION_RATE_LIMIT_BALLOT_VERIFY_LIMIT = _env_int(
    "ELECTION_RATE_LIMIT_BALLOT_VERIFY_LIMIT",
    default=30,
)
ELECTION_RATE_LIMIT_BALLOT_VERIFY_WINDOW_SECONDS = _env_int(
    "ELECTION_RATE_LIMIT_BALLOT_VERIFY_WINDOW_SECONDS",
    default=60,
)

ELECTION_RATE_LIMIT_VOTE_SUBMIT_LIMIT = _env_int(
    "ELECTION_RATE_LIMIT_VOTE_SUBMIT_LIMIT",
    default=20,
)
ELECTION_RATE_LIMIT_VOTE_SUBMIT_WINDOW_SECONDS = _env_int(
    "ELECTION_RATE_LIMIT_VOTE_SUBMIT_WINDOW_SECONDS",
    default=60 * 60,
)

ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT = _env_int(
    "ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT",
    default=10,
)
ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS = _env_int(
    "ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS",
    default=10 * 60,
)

# Membership workflow
MEMBERSHIP_EXPIRING_SOON_DAYS = _env_int("MEMBERSHIP_EXPIRING_SOON_DAYS", default=90)
MEMBERSHIP_VALIDITY_DAYS = _env_int("MEMBERSHIP_VALIDITY_DAYS", default=365)
MEMBERSHIP_EMBARGOED_COUNTRY_CODES = _env_list(
    "MEMBERSHIP_EMBARGOED_COUNTRY_CODES",
    default=["CU", "IR", "KP", "MM", "RU", "SY", "VE"],
)
MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME",
    default="membership-expiring-soon",
) or "membership-expiring-soon"
MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME",
    default="membership-expired",
) or "membership-expired"
MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME",
    default="membership-request-submitted",
) or "membership-request-submitted"
MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME",
    default="membership-request-approved-individual",
) or "membership-request-approved-individual"
MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME",
    default="membership-request-rejected",
) or "membership-request-rejected"

MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME",
    default="membership-request-rfi",
) or "membership-request-rfi"

MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME",
    default="membership-committee-pending-requests",
) or "membership-committee-pending-requests"

MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME = _env_str(
    "MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME",
    default="membership-committee-embargoed-members",
) or "membership-committee-embargoed-members"

PASSWORD_RESET_TOKEN_TTL_SECONDS = _env_int("PASSWORD_RESET_TOKEN_TTL_SECONDS", default=60 * 60)
PASSWORD_RESET_EMAIL_TEMPLATE_NAME = _env_str(
    "PASSWORD_RESET_EMAIL_TEMPLATE_NAME",
    default="password-reset",
) or "password-reset"
PASSWORD_RESET_SUCCESS_EMAIL_TEMPLATE_NAME = _env_str(
    "PASSWORD_RESET_SUCCESS_EMAIL_TEMPLATE_NAME",
    default="password-reset-success",
) or "password-reset-success"

ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME = _env_str(
    "ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME",
    default="election-voting-credential",
) or "election-voting-credential"

ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME = _env_str(
    "ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME",
    default="election-vote-receipt",
) or "election-vote-receipt"

# Queue all Django mail through django-post_office.
EMAIL_BACKEND = 'post_office.EmailBackend'

# Configure post_office delivery backends.
# - DEBUG: deliver immediately via SMTP to Mailhog.
# - non-DEBUG: default delivery backend is AWS SES (django-ses); delivery still
#   requires running `python manage.py send_queued_mail` (or enabling Celery).
POST_OFFICE = {
    'DEFAULT_PRIORITY': 'now' if DEBUG else 'medium',
    'MESSAGE_ID_ENABLED': True,
    'MAX_RETRIES': 4,
    'RETRY_INTERVAL': datetime.timedelta(minutes=5),
    'TEMPLATE_ENGINE': 'post_office',
    'BACKENDS': {
        'default': 'django.core.mail.backends.smtp.EmailBackend' if DEBUG else 'django_ses.SESBackend',
        'smtp': 'django.core.mail.backends.smtp.EmailBackend',
        'ses': 'django_ses.SESBackend',
    },
}

# django-ses (AWS SES)
# Used as the production delivery backend (see POST_OFFICE['BACKENDS']).
# Also provides a stats dashboard and an SNS event webhook (bounces, complaints,
# deliveries, opens, clicks).
AWS_SES_REGION_NAME = _env_str("AWS_SES_REGION_NAME", default="us-east-1") or "us-east-1"
AWS_SES_REGION_ENDPOINT = os.environ.get("AWS_SES_REGION_ENDPOINT", f"email.{AWS_SES_REGION_NAME}.amazonaws.com")

# Signature verification is recommended for production SNS webhooks. For local
# dev, allowing unsigned test payloads is convenient.
AWS_SES_VERIFY_EVENT_SIGNATURES = _env_bool("AWS_SES_VERIFY_EVENT_SIGNATURES", default=not DEBUG)

# Restrict certificate download domains for SNS signature verification.
# Prefer the full SNS domain for your region.
AWS_SNS_EVENT_CERT_TRUSTED_DOMAINS = _env_list(
    "AWS_SNS_EVENT_CERT_TRUSTED_DOMAINS",
    default=[f"sns.{AWS_SES_REGION_NAME}.amazonaws.com"],
)

# Optional blacklisting behavior: when enabled, bounce/complaint signals will
# add recipients to django_ses.BlacklistedEmail and the SES backend will avoid
# sending to them.
AWS_SES_USE_BLACKLIST = _env_bool("AWS_SES_USE_BLACKLIST", default=not DEBUG)
AWS_SES_ADD_BOUNCE_TO_BLACKLIST = _env_bool("AWS_SES_ADD_BOUNCE_TO_BLACKLIST", default=not DEBUG)
AWS_SES_ADD_COMPLAINT_TO_BLACKLIST = _env_bool("AWS_SES_ADD_COMPLAINT_TO_BLACKLIST", default=not DEBUG)

# Optional: tag outgoing mail with a configuration set for event publishing.
AWS_SES_CONFIGURATION_SET = (_env_str("AWS_SES_CONFIGURATION_SET", default="") or "").strip() or None

# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Security
# Keep these production-oriented but configurable; many deployments sit behind
# a TLS-terminating proxy/load balancer.
if not DEBUG:
    # If you're behind a reverse proxy that sets X-Forwarded-Proto.
    if _env_bool("SECURE_PROXY_SSL", default=True):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", default=False)
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = _env_bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = _env_str("SECURE_REFERRER_POLICY", default="same-origin") or "same-origin"

    # HSTS is opt-in by default because it can brick HTTP-only deployments.
    SECURE_HSTS_SECONDS = _env_int("SECURE_HSTS_SECONDS", default=0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
    SECURE_HSTS_PRELOAD = _env_bool("SECURE_HSTS_PRELOAD", default=False)

    CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS", default=[])

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'

# Production collectstatic target.
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded media (e.g. Organization.logo)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# S3-backed storage for uploaded media.
STORAGES = {
    "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

AWS_STORAGE_BUCKET_NAME = _env_str("AWS_STORAGE_BUCKET_NAME", default="") or ""
AWS_S3_REGION_NAME = _env_str("AWS_S3_REGION_NAME", default="us-east-1") or "us-east-1"
AWS_S3_ENDPOINT_URL = (_env_str("AWS_S3_ENDPOINT_URL", default="") or "").strip() or None

# Optional: separate the URL used in rendered pages from the internal API endpoint.
# This is useful in docker-compose where Django must reach MinIO via the service
# name (e.g. http://minio:9000) but browsers reach it via localhost port mapping.
#
# Historically we used AWS_S3_URL_PROTOCOL + AWS_S3_CUSTOM_DOMAIN for this, but
# those settings have been replaced by PUBLIC_BASE_URL + AWS_STORAGE_BUCKET_NAME.
_public_base_url_raw = str(PUBLIC_BASE_URL or "").strip()
if "://" not in _public_base_url_raw:
    _public_base_url_raw = f"https://{_public_base_url_raw}"

_aws_s3_domain_raw = _env_str("AWS_S3_DOMAIN", default="") or ""
if not _ALLOW_MISSING_RUNTIME_SECRETS:
    if not AWS_STORAGE_BUCKET_NAME:
        raise ImproperlyConfigured("AWS_STORAGE_BUCKET_NAME must be set.")
    if not _aws_s3_domain_raw:
        raise ImproperlyConfigured("AWS_S3_DOMAIN must be set.")
else:
    if not AWS_STORAGE_BUCKET_NAME:
        AWS_STORAGE_BUCKET_NAME = "migrations-placeholder"
    if not _aws_s3_domain_raw:
        _aws_s3_domain_raw = "http://localhost"

_aws_s3_domain = urlsplit(_aws_s3_domain_raw)
AWS_S3_URL_PROTOCOL = f"{_aws_s3_domain.scheme}:"
_aws_s3_base_domain = (_aws_s3_domain.netloc + _aws_s3_domain.path.rstrip("/")).strip("/")
AWS_S3_CUSTOM_DOMAIN = f"{_aws_s3_base_domain}/{AWS_STORAGE_BUCKET_NAME}"

# MinIO compatibility and predictable URLs.
AWS_S3_ADDRESSING_STYLE = _env_str("AWS_S3_ADDRESSING_STYLE", default="path") or "path"
AWS_QUERYSTRING_AUTH = _env_bool("AWS_QUERYSTRING_AUTH", default=False)
AWS_DEFAULT_ACL = None

# Profile chat link formatting (Noggin-style).
CHAT_NETWORKS = {
    "mattermost": {
        "default_server": _env_str("CHAT_MATTERMOST_DEFAULT_SERVER", default="chat.almalinux.org")
        or "chat.almalinux.org",
        "default_team": _env_str("CHAT_MATTERMOST_DEFAULT_TEAM", default="almalinux") or "almalinux",
    },
    "irc": {"default_server": _env_str("CHAT_IRC_DEFAULT_SERVER", default="irc.libera.chat") or "irc.libera.chat"},
    "matrix": {"default_server": _env_str("CHAT_MATRIX_DEFAULT_SERVER", default="matrix.org") or "matrix.org"},
}

# Optional query string appended to matrix.to links (Element web instance).
CHAT_MATRIX_TO_ARGS = _env_str(
    "CHAT_MATRIX_TO_ARGS",
    default="web-instance[element.io]=app.element.io",
) or "web-instance[element.io]=app.element.io"

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# django-avatar configuration
# Avoid the PrimaryAvatarProvider (DB-backed Avatar records) to prevent ORM
# lookups against AUTH_USER_MODEL for request.user objects.
AVATAR_PROVIDERS = (
    'avatar.providers.GravatarAvatarProvider',
    'avatar.providers.LibRAvatarProvider',
    'avatar.providers.DefaultAvatarProvider',
)
AVATAR_GRAVATAR_DEFAULT = _env_str("AVATAR_GRAVATAR_DEFAULT", default="identicon") or "identicon"

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication Backends
AUTHENTICATION_BACKENDS = [
    'core.backends.FreeIPAAuthBackend',
]

# FreeIPA Configuration
FREEIPA_HOST = _env_str("FREEIPA_HOST", default="ipa.demo1.freeipa.org") or "ipa.demo1.freeipa.org"
FREEIPA_VERIFY_SSL = _env_bool("FREEIPA_VERIFY_SSL", default=True)
FREEIPA_SERVICE_USER = _env_str("FREEIPA_SERVICE_USER", default="admin") or "admin"
FREEIPA_SERVICE_PASSWORD = _env_str("FREEIPA_SERVICE_PASSWORD", default="") or ""
if not _ALLOW_MISSING_RUNTIME_SECRETS and not FREEIPA_SERVICE_PASSWORD:
    raise ImproperlyConfigured("FREEIPA_SERVICE_PASSWORD must be set.")
FREEIPA_ADMIN_GROUP = _env_str("FREEIPA_ADMIN_GROUP", default="admins") or "admins"
FREEIPA_MEMBERSHIP_COMMITTEE_GROUP = _env_str("FREEIPA_MEMBERSHIP_COMMITTEE_GROUP", default="membership-committee") or "membership-committee"
FREEIPA_ELECTION_COMMITTEE_GROUP = _env_str("FREEIPA_ELECTION_COMMITTEE_GROUP", default="election-committee") or "election-committee"

# Users to exclude from directory listings/searches (these accounts can still log in).
# This prevents service/admin accounts from appearing in user pickers, search results, etc.
_freeipa_filtered_usernames_raw = _env_str("FREEIPA_FILTERED_USERNAMES", default="") or ""
_freeipa_filtered_usernames_extra = {
    u.strip().lower() for u in _freeipa_filtered_usernames_raw.split(",") if u.strip()
}
FREEIPA_FILTERED_USERNAMES = frozenset(
    {
        "admin",
        str(FREEIPA_SERVICE_USER or "").strip().lower(),
        *_freeipa_filtered_usernames_extra,
    }
)

# Reuse the FreeIPA service-account client across requests (per worker thread).
# This avoids repeated logins for admin/selfservice pages that trigger multiple
# FreeIPA reads, and retries automatically if the session expires.
FREEIPA_SERVICE_CLIENT_REUSE_ACROSS_REQUESTS = _env_bool(
    "FREEIPA_SERVICE_CLIENT_REUSE_ACROSS_REQUESTS",
    default=True,
)

# Development convenience: silence urllib3's InsecureRequestWarning spam when
# intentionally running with verify_ssl disabled (e.g. local FreeIPA with self-signed cert).
if FREEIPA_VERIFY_SSL is False:
    try:
        import urllib3
        from urllib3.exceptions import InsecureRequestWarning

        urllib3.disable_warnings(InsecureRequestWarning)
    except Exception:
        # Best-effort; if urllib3 isn't available/changes, don't break startup.
        pass

# Registration
# Inspired by Noggin's stage-user registration flow.
REGISTRATION_OPEN = _env_bool("REGISTRATION_OPEN", default=True)
# Email validation token TTL.
EMAIL_VALIDATION_TOKEN_TTL_SECONDS = _env_int(
    "EMAIL_VALIDATION_TOKEN_TTL_SECONDS",
    default=60 * 60 * 24,
)
REGISTRATION_EMAIL_TEMPLATE_NAME = "registration-email-validation"
EMAIL_VALIDATION_EMAIL_TEMPLATE_NAME = "settings-email-validation"

# Map FreeIPA groups to Django permissions
# Format: {'freeipa_group_name': {'app_label.permission_codename', ...}}
FREEIPA_GROUP_PERMISSIONS = {
    'content_editors': {'core.add_article', 'core.change_article'},
    'moderators': {'core.delete_article'},
}

# Caching
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
        'TIMEOUT': 300,
    }
}

# Logging
# Ensure app logs (including FreeIPA integration) are visible in container stdout.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {},
    'formatters': {
        'console': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'loggers': {
        # Our app
        'core': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        # FreeIPA client libs can be noisy; keep them at INFO by default.
        'python_freeipa': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Django request errors - log everything including 4xx errors
        'django.request': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        # Access logs from `runserver`.
        'django.server': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Django security events
        'django.security': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    # Root logger catches everything else
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
