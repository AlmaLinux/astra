import datetime
from collections.abc import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAGroup, FreeIPAUser
from core.email_context import (
    membership_committee_email_context,
    organization_sponsor_email_context,
    system_email_context,
    user_email_context,
)
from core.models import FreeIPAPermissionGrant, MembershipType, Organization
from core.templated_email import queue_templated_email


def _join_base_url_with_path(*, path: str, base_url: str | None = None) -> str:
    base = (base_url if base_url is not None else settings.PUBLIC_BASE_URL) or ""
    normalized_base = str(base).strip().rstrip("/")
    if not normalized_base:
        # Fallback for misconfiguration; prefer sending a link over crashing.
        return path
    return f"{normalized_base}{path}"


def membership_extend_url(*, membership_type_code: str, base_url: str | None = None) -> str:
    path = f"{reverse('membership-request')}?{urlencode({'membership_type': membership_type_code})}"
    return _join_base_url_with_path(path=path, base_url=base_url)


def membership_requests_url(*, base_url: str | None = None) -> str:
    path = reverse("membership-requests")
    return _join_base_url_with_path(path=path, base_url=base_url)


def organization_membership_request_url(
    *,
    organization_id: int,
    membership_type_code: str | None = None,
    base_url: str | None = None,
) -> str:
    path = reverse("organization-membership-request", kwargs={"organization_id": organization_id})
    normalized_type_code = str(membership_type_code or "").strip()
    if normalized_type_code:
        path = f"{path}?{urlencode({'membership_type': normalized_type_code})}"

    return _join_base_url_with_path(path=path, base_url=base_url)


def organization_sponsor_notification_recipient_email(
    *, organization: Organization, notification_kind: str,
) -> tuple[str, str | None]:
    representative_username = str(organization.representative or "").strip()
    if representative_username:
        try:
            representative = FreeIPAUser.get(representative_username)
        except Exception:
            representative = None
        if representative is not None:
            address = str(representative.email or "").strip()
            if address:
                return address, None

    fallback_address = str(organization.primary_contact_email() or "").strip()
    if fallback_address:
        return fallback_address, None

    return "", (
        f"No recipient resolved for organization id={organization.pk}; "
        f"notification_kind={notification_kind}"
    )


def would_queue_membership_pending_requests_notification(
    *, force: bool, template_name: str, today: datetime.date | None = None,
) -> bool:
    if force:
        return True

    from post_office.models import Email

    target_date = today if today is not None else timezone.localdate()
    if target_date.weekday() == 0:
        # Monday cadence is once per day.
        return not Email.objects.filter(
            template__name=template_name,
            created__date=target_date,
        ).exists()

    this_weeks_monday = target_date - datetime.timedelta(days=target_date.weekday())
    # Tue-Sun cadence is once since Monday.
    return not Email.objects.filter(
        template__name=template_name,
        created__date__gte=this_weeks_monday,
    ).exists()


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


def would_queue_membership_notification(
    *,
    force: bool,
    template_name: str,
    recipient_email: str,
    membership_type: MembershipType,
    organization: Organization | None = None,
    today: datetime.date | None = None,
) -> bool:
    if force:
        return True

    address = str(recipient_email or "").strip()
    if not address:
        return False

    extra_filters: dict[str, object] = {
        "context__membership_type_code": membership_type.code,
    }
    if organization is not None:
        extra_filters["context__organization_id"] = organization.pk

    return not already_sent_today(
        template_name=template_name,
        recipient_email=address,
        extra_filters=extra_filters,
        today=today,
    )


def committee_recipient_emails_for_permission(
    *,
    permission: str,
    group_getter: Callable[[str], FreeIPAGroup | None] | None = None,
    user_getter: Callable[[str], FreeIPAUser | None] | None = None,
) -> list[str]:
    resolved_group_getter = group_getter or FreeIPAGroup.get
    resolved_user_getter = user_getter or FreeIPAUser.get
    recipients, warnings = committee_recipient_emails_for_permission_graceful(
        permission=permission,
        group_getter=resolved_group_getter,
        user_getter=resolved_user_getter,
    )
    if recipients:
        return recipients
    if warnings:
        raise ValueError(warnings[0])
    raise ValueError(f"No email addresses found for any principals with {permission}")


def committee_recipient_emails_for_permission_graceful(
    *,
    permission: str,
    group_getter: Callable[[str], FreeIPAGroup | None] | None = None,
    user_getter: Callable[[str], FreeIPAUser | None] | None = None,
) -> tuple[list[str], list[str]]:
    resolved_group_getter = group_getter or FreeIPAGroup.get
    resolved_user_getter = user_getter or FreeIPAUser.get

    warnings: list[str] = []
    grants = list(FreeIPAPermissionGrant.objects.filter(permission=permission))
    if not grants:
        return [], [f"No FreeIPA grants exist for permission: {permission}"]

    direct_usernames: list[str] = []
    group_names: list[str] = []
    for grant in grants:
        if grant.principal_type == FreeIPAPermissionGrant.PrincipalType.user:
            direct_usernames.append(grant.principal_name)
        elif grant.principal_type == FreeIPAPermissionGrant.PrincipalType.group:
            group_names.append(grant.principal_name)

    expanded_usernames: list[str] = [*direct_usernames]
    for group_name in group_names:
        group = resolved_group_getter(group_name)
        if group is None:
            warnings.append(
                f"Unable to load FreeIPA group referenced by permission grant: {group_name}"
            )
            continue
        expanded_usernames.extend(list(group.members))

    recipients: list[str] = []
    seen: set[str] = set()
    for username in expanded_usernames:
        user = resolved_user_getter(username)
        if user is None or not user.email:
            continue
        addr = str(user.email or "").strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        recipients.append(addr)

    if not recipients:
        warnings.append(f"No email addresses found for any principals with {permission}")

    return recipients, warnings


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
    if not would_queue_membership_notification(
        force=force,
        template_name=template_name,
        recipient_email=address,
        membership_type=membership_type,
        organization=organization,
        today=today,
    ):
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
            "organization_id": organization.pk,
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
