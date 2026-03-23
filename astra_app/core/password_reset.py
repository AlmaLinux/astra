import datetime
import logging
import re
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from core.email_context import user_email_context
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.templated_email import queue_templated_email
from core.tokens import make_password_reset_token
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)

_DATETIME_MARKER_DICT_REPR_PATTERN = re.compile(
    r"""\{\s*(?P<key_quote>['\"])__datetime__(?P=key_quote)\s*:\s*(?P<value_quote>['\"])(?P<value>[^'\"]+)(?P=value_quote)\s*\}\s*"""
)


def normalize_last_password_change(value: object) -> str:
    """Return a stable string form for FreeIPA password-change timestamps."""
    if value is None:
        return ""

    if isinstance(value, dict):
        normalized_dict_dt = _normalize_str(value.get("__datetime__"))
        if normalized_dict_dt:
            return normalized_dict_dt
        return _normalize_str(value)

    normalized_value = _normalize_str(value)
    if not normalized_value:
        return ""

    if "__datetime__" not in normalized_value or not normalized_value.startswith("{"):
        return normalized_value

    marker_match = _DATETIME_MARKER_DICT_REPR_PATTERN.fullmatch(normalized_value)
    if marker_match is not None:
        normalized_marker_value = _normalize_str(marker_match.group("value"))
        if normalized_marker_value:
            return normalized_marker_value

    return normalized_value


def password_reset_confirm_url(*, request: HttpRequest, token: str) -> str:
    return request.build_absolute_uri(reverse("password-reset-confirm")) + f"?token={quote(token)}"


def password_reset_login_url(*, request: HttpRequest) -> str:
    return request.build_absolute_uri(reverse("login"))


def send_password_reset_email(
    *,
    request: HttpRequest,
    username: str,
    email: str,
    last_password_change: str,
    invitation_token: str | None = None,
) -> None:
    canonical_last_password_change = normalize_last_password_change(last_password_change)

    token_payload: dict[str, str] = {
        "u": username,
        "e": email,
        "lpc": canonical_last_password_change,
    }
    normalized_invitation_token = _normalize_str(invitation_token)
    if normalized_invitation_token:
        token_payload["i"] = normalized_invitation_token

    token = make_password_reset_token(token_payload)
    reset_url = password_reset_confirm_url(request=request, token=token)

    ttl_seconds = settings.PASSWORD_RESET_TOKEN_TTL_SECONDS
    ttl_minutes = max(1, int((ttl_seconds + 59) / 60))
    valid_until = timezone.now() + datetime.timedelta(seconds=ttl_seconds)
    valid_until_utc = valid_until.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    base_ctx = user_email_context(username=username)
    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.PASSWORD_RESET_EMAIL_TEMPLATE_NAME,
        context={
            **base_ctx,
            "reset_url": reset_url,
            "ttl_minutes": ttl_minutes,
            "valid_until_utc": valid_until_utc,
        },
    )


def send_password_reset_success_email(*, request: HttpRequest, username: str, email: str) -> None:
    base_ctx = user_email_context(username=username)
    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.PASSWORD_RESET_SUCCESS_EMAIL_TEMPLATE_NAME,
        context={
            **base_ctx,
            "login_url": password_reset_login_url(request=request),
        },
    )


def find_user_for_password_reset(identifier: str) -> FreeIPAUser | None:
    value = _normalize_str(identifier)
    if not value:
        logger.debug("Password reset lookup skipped: empty identifier")
        return None

    if "@" in value:
        logger.debug(
            "Password reset lookup attempt identifier_type=email identifier=%s",
            value,
        )
        try:
            user = FreeIPAUser.find_by_email(value)
            if user is None:
                logger.debug(
                    "Password reset lookup result identifier_type=email identifier=%s result=not_found",
                    value,
                )
                return None

            # Canonicalize through username lookup so request-time lpc matches
            # the confirm-time source used by password_reset_confirm().
            username = _normalize_str(user.username)
            resolved_email = _normalize_str(user.email).lower()
            logger.debug(
                "Password reset lookup email result identifier=%s resolved_username=%s resolved_email=%s",
                value,
                username,
                resolved_email,
            )
            if not username:
                logger.warning(
                    "Password reset lookup canonicalization failed "
                    "identifier_type=email identifier=%s reason=missing_username",
                    value,
                )
                return None

            canonical_user = FreeIPAUser.get(username)
            if canonical_user is None:
                logger.warning(
                    "Password reset lookup canonicalization failed "
                    "identifier_type=email identifier=%s resolved_username=%s reason=canonical_user_missing",
                    value,
                    username,
                )
                return None

            canonical_username = _normalize_str(canonical_user.username)
            canonical_email = _normalize_str(canonical_user.email).lower()
            logger.debug(
                "Password reset lookup success identifier_type=email identifier=%s "
                "resolved_username=%s resolved_email=%s canonical_username=%s canonical_email=%s",
                value,
                username,
                resolved_email,
                canonical_username,
                canonical_email,
            )
            return canonical_user
        except Exception:
            logger.exception(
                "Password reset lookup by email failed identifier=%s",
                value,
                extra=current_exception_log_fields(),
            )
            return None

    logger.debug(
        "Password reset lookup attempt identifier_type=username identifier=%s",
        value,
    )
    try:
        user = FreeIPAUser.get(value)
        if user is None:
            logger.debug(
                "Password reset lookup result identifier_type=username identifier=%s result=not_found",
                value,
            )
            return None

        resolved_username = _normalize_str(user.username)
        resolved_email = _normalize_str(user.email).lower()
        logger.debug(
            "Password reset lookup success identifier_type=username identifier=%s "
            "resolved_username=%s resolved_email=%s",
            value,
            resolved_username,
            resolved_email,
        )
        return user
    except Exception:
        logger.exception(
            "Password reset lookup by username failed identifier=%s",
            value,
            extra=current_exception_log_fields(),
        )
        return None


def set_freeipa_password(*, username: str, new_password: str) -> None:
    client = FreeIPAUser.get_client()
    try:
        client.user_mod(username, o_userpassword=new_password)
    except TypeError:
        client.user_mod(a_uid=username, o_userpassword=new_password)
