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


def already_sent_today(
    *,
    template_name: str,
    recipient_email: str | None = None,
    extra_filters: dict[str, object] | None = None,
    today: datetime.date | None = None,
) -> bool:
    from post_office.models import Email

    target_date = today if today is not None else timezone.localdate()
    filters: dict[str, object] = {
        "template__name": template_name,
        "created__date": target_date,
    }
    if recipient_email:
        filters["to"] = recipient_email
    if extra_filters:
        filters |= extra_filters
    return Email.objects.filter(**filters).exists()


def send_membership_notification(
    *,
    recipient_email: str,
    membership_type: MembershipType,
    template_name: str,
    expires_at: datetime.datetime | None,
    username: str | None = None,
    organization: Organization | None = None,
    days: int | None = None,
    force: bool = False,
    base_url: str | None = None,
    tz_name: str | None = None,
    extra_context: dict[str, str] | None = None,
) -> bool:
    """Queue a templated email via django-post-office.

    Returns True if an email was queued, False if skipped (e.g. deduped).
    """

    address = str(recipient_email or "").strip()
    if not address:
        return False

    today = timezone.localdate()

    extra_filters = {
        "context__membership_type_code": membership_type.code,
    }
    if organization is not None:
        extra_filters["context__organization_id"] = organization.id

    if not force:
        already_sent = already_sent_today(
            template_name=template_name,
            recipient_email=address,
            extra_filters=extra_filters,
            today=today,
        )
        if already_sent:
            return False

    if organization is not None:
        base_ctx = organization_sponsor_email_context(organization=organization)
        committee_email = str(settings.MEMBERSHIP_COMMITTEE_EMAIL or "").strip()
        cc = [committee_email] if committee_email else None
        reply_to = [committee_email] if committee_email else None
        context = {
            **base_ctx,
            **membership_committee_email_context(),
            **system_email_context(),
            "organization_id": organization.id,
            "organization_name": str(organization.name or ""),
            "membership_type": membership_type.name,
            "membership_type_code": membership_type.code,
            "expires_at": _format_expires_at(expires_at=expires_at, tz_name=tz_name),
            "days": days,
        }
    else:
        if username is None:
            return False

        base_ctx = user_email_context(username=username)
        cc = None
        reply_to = [settings.MEMBERSHIP_COMMITTEE_EMAIL]
        context = {
            **base_ctx,
            **membership_committee_email_context(),
            "membership_type": membership_type.name,
            "membership_type_code": membership_type.code,
            "extend_url": membership_extend_url(
                membership_type_code=membership_type.code,
                base_url=base_url,
            ),
            "expires_at": _format_expires_at(expires_at=expires_at, tz_name=tz_name),
            "days": days,
        }

    if extra_context:
        context |= extra_context

    queue_templated_email(
        recipients=[address],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=template_name,
        context=context,
        cc=cc,
        reply_to=reply_to,
    )

    return True
