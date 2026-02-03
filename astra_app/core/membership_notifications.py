from __future__ import annotations

import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from core.email_context import (
    membership_committee_email_context,
    organization_sponsor_email_context,
    system_email_context,
    user_email_context,
)
from core.models import MembershipType, Organization
from core.templated_email import queue_templated_email


def membership_extend_url(*, membership_type_code: str, base_url: str | None = None) -> str:
    path = f"/membership/request/?{urlencode({'membership_type': membership_type_code})}"

    base = (base_url if base_url is not None else settings.PUBLIC_BASE_URL) or ""
    base = str(base).strip().rstrip("/")
    if not base:
        # Fallback for misconfiguration; prefer sending a link over crashing.
        return path
    return f"{base}{path}"


def _format_expires_at(*, expires_at: datetime.datetime | None, tz_name: str | None) -> str:
    if expires_at is None:
        return ""

    target_tz_name = str(tz_name or "").strip() or "UTC"
    try:
        tzinfo = ZoneInfo(target_tz_name)
    except Exception:
        target_tz_name = "UTC"
        tzinfo = ZoneInfo("UTC")

    local = timezone.localtime(expires_at, timezone=tzinfo)
    return f"{local.strftime('%b %d, %Y %H:%M')} ({target_tz_name})"


def send_membership_notification(
    *,
    recipient_email: str,
    username: str,
    membership_type: MembershipType,
    template_name: str,
    expires_at: datetime.datetime | None,
    days: int | None = None,
    force: bool = False,
    base_url: str | None = None,
    tz_name: str | None = None,
    user_context: dict[str, str] | None = None,
) -> bool:
    """Queue a templated email via django-post-office.

    Returns True if an email was queued, False if skipped (e.g. deduped).
    """

    address = str(recipient_email or "").strip()
    if not address:
        return False

    today = timezone.localdate()

    if not force:
        from post_office.models import Email

        already_sent = Email.objects.filter(
            to=address,
            template__name=template_name,
            context__membership_type_code=membership_type.code,
            created__date=today,
        ).exists()
        if already_sent:
            return False

    base_ctx = user_context if user_context is not None else user_email_context(username=username)

    queue_templated_email(
        recipients=[address],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=template_name,
        context={
            **base_ctx,
            **membership_committee_email_context(),
            "membership_type": membership_type.name,
            "membership_type_code": membership_type.code,
            "extend_url": membership_extend_url(membership_type_code=membership_type.code, base_url=base_url),
            "expires_at": _format_expires_at(expires_at=expires_at, tz_name=tz_name),
            "days": days,
        },
        reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
    )

    return True


def send_organization_sponsorship_notification(
    *,
    recipient_email: str,
    organization: Organization,
    membership_type: MembershipType,
    template_name: str,
    expires_at: datetime.datetime | None,
    extend_url: str,
    days: int | None = None,
    force: bool = False,
    sponsor_context: dict[str, str] | None = None,
) -> bool:
    """Queue a templated email for organization sponsorship expiry warnings.

    Returns True if an email was queued, False if skipped (e.g. deduped).
    """

    address = str(recipient_email or "").strip()
    if not address:
        return False

    today = timezone.localdate()

    if not force:
        from post_office.models import Email

        already_sent = Email.objects.filter(
            to=address,
            template__name=template_name,
            context__organization_id=organization.id,
            context__membership_type_code=membership_type.code,
            created__date=today,
        ).exists()
        if already_sent:
            return False

    base_ctx = (
        sponsor_context
        if sponsor_context is not None
        else organization_sponsor_email_context(organization=organization)
    )

    committee_email = str(settings.MEMBERSHIP_COMMITTEE_EMAIL or "").strip()
    cc = [committee_email] if committee_email else None
    reply_to = [committee_email] if committee_email else None

    queue_templated_email(
        recipients=[address],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=template_name,
        context={
            **base_ctx,
            **membership_committee_email_context(),
            **system_email_context(),
            "organization_id": organization.id,
            "organization_name": str(organization.name or ""),
            "membership_type": membership_type.name,
            "membership_type_code": membership_type.code,
            "extend_url": extend_url,
            "expires_at": _format_expires_at(expires_at=expires_at, tz_name="UTC"),
            "days": days,
        },
        cc=cc,
        reply_to=reply_to,
    )

    return True
