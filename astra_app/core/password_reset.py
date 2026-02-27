import datetime
import logging
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from core.email_context import user_email_context
from core.freeipa.user import FreeIPAUser
from core.templated_email import queue_templated_email
from core.tokens import make_signed_token, read_signed_token
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)

PASSWORD_RESET_TOKEN_PURPOSE = "password-reset"

def read_password_reset_token(token: str) -> dict[str, object]:
    payload = read_signed_token(token, max_age_seconds=settings.PASSWORD_RESET_TOKEN_TTL_SECONDS)
    if _normalize_str(payload.get("p")) != PASSWORD_RESET_TOKEN_PURPOSE:
        raise signing.BadSignature("Wrong token purpose")
    return payload


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
    token_payload: dict[str, str] = {
        "p": PASSWORD_RESET_TOKEN_PURPOSE,
        "u": username,
        "e": email,
        "lpc": last_password_change,
    }
    normalized_invitation_token = _normalize_str(invitation_token)
    if normalized_invitation_token:
        token_payload["i"] = normalized_invitation_token

    token = make_signed_token(token_payload)
    reset_url = password_reset_confirm_url(request=request, token=token)

    ttl_seconds = settings.PASSWORD_RESET_TOKEN_TTL_SECONDS
    ttl_minutes = max(1, int((ttl_seconds + 59) / 60))
    valid_until = timezone.now() + datetime.timedelta(seconds=ttl_seconds)
    valid_until_utc = valid_until.astimezone(datetime.UTC).strftime("%H:%M")

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
        return None

    if "@" in value:
        try:
            return FreeIPAUser.find_by_email(value)
        except Exception:
            logger.exception("Password reset lookup by email failed")
            return None

    try:
        return FreeIPAUser.get(value)
    except Exception:
        logger.exception("Password reset lookup by username failed")
        return None


def set_freeipa_password(*, username: str, new_password: str) -> None:
    client = FreeIPAUser.get_client()
    try:
        client.user_mod(username, o_userpassword=new_password)
    except TypeError:
        client.user_mod(a_uid=username, o_userpassword=new_password)
