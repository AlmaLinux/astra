import datetime
import logging
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.email_context import (
    membership_committee_email_context,
    organization_sponsor_email_context,
    system_email_context,
    user_email_context,
)
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.membership import visible_committee_membership_requests
from core.membership_notes import CUSTOS
from core.models import FreeIPAPermissionGrant, Membership, MembershipRequest, MembershipType, Organization
from core.public_urls import build_public_absolute_url
from core.sponsorship_invoices import InvoiceTerm, invoice_eligible, invoice_email_parts
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)


def membership_extend_url(*, membership_type_code: str, base_url: str | None = None) -> str:
    path = f"{reverse('membership-request')}?{urlencode({'membership_type': membership_type_code})}"
    return build_public_absolute_url(path=path, on_missing="relative", base_url=base_url)


def membership_requests_url(*, base_url: str | None = None) -> str:
    path = reverse("membership-requests")
    return build_public_absolute_url(path=path, on_missing="relative", base_url=base_url)


def organization_membership_request_url(
    *,
    organization_id: int,
    membership_type_code: str | None = None,
    base_url: str | None = None,
) -> str:
    path = reverse("organization-membership-request", kwargs={"organization_id": organization_id})
    if membership_type_code:
        path = f"{path}?{urlencode({'membership_type': membership_type_code})}"

    return build_public_absolute_url(path=path, on_missing="relative", base_url=base_url)


def organization_sponsor_notification_recipient_email(
    *, organization: Organization, notification_kind: str,
) -> tuple[str, str | None]:
    representative_username = organization.representative
    if representative_username:
        try:
            representative = FreeIPAUser.get(representative_username, respect_privacy=False)
        except Exception:
            representative = None
        if representative is not None and representative.email:
            return representative.email, None

    fallback_address = organization.primary_contact_email()
    if fallback_address:
        return fallback_address, None

    return "", (
        f"No recipient resolved for organization id={organization.pk}; "
        f"notification_kind={notification_kind}"
    )


def oldest_pending_membership_request_wait_time(
    *,
    live_usernames: Iterable[str] | None = None,
    live_users_by_username: Mapping[str, FreeIPAUser] | None = None,
) -> int | None:
    oldest_request = visible_committee_membership_requests(
        MembershipRequest.objects.select_related("requested_organization")
        .filter(status=MembershipRequest.Status.pending)
        .order_by("requested_at"),
        live_usernames=live_usernames,
        live_users_by_username=live_users_by_username,
    )
    if not oldest_request:
        return None

    oldest_request = oldest_request[0]

    oldest_wait_time = timezone.now() - oldest_request.requested_at
    if oldest_wait_time < datetime.timedelta(days=12):
        return None

    return oldest_wait_time.days


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


def admin_group_emails(
    *,
    user_getter: Callable[[str], FreeIPAUser | None] | None = None,
) -> list[str]:
    """Return the email addresses of the FreeIPA admin group, for operational alerts.

    Logs an error and returns an empty list when nobody can be reached.
    """
    resolved_user_getter = user_getter or FreeIPAUser.get
    admin_group_cn = settings.FREEIPA_ADMIN_GROUP
    admin_group = FreeIPAGroup.get(admin_group_cn) if admin_group_cn else None
    if admin_group is None:
        logger.error("admin_group_emails: admin_group_missing group=%s", admin_group_cn)
        return []

    emails: set[str] = set()
    for username in admin_group.members:
        user = resolved_user_getter(username)
        if user is not None and user.email:
            emails.add(user.email)
    if not emails:
        logger.error("admin_group_emails: no_admin_recipients group=%s", admin_group_cn)
    return sorted(emails, key=str.lower)


def committee_recipient_emails_for_permission(
    *,
    permission: str,
    group_getter: Callable[[str], FreeIPAGroup | None] | None = None,
    user_getter: Callable[[str], FreeIPAUser | None] | None = None,
) -> list[str]:
    resolved_group_getter = group_getter or FreeIPAGroup.get
    if user_getter is None:
        def resolved_user_getter(username: str) -> FreeIPAUser | None:
            return FreeIPAUser.get(username, respect_privacy=False)
    else:
        resolved_user_getter = user_getter
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
    if user_getter is None:
        def resolved_user_getter(username: str) -> FreeIPAUser | None:
            return FreeIPAUser.get(username, respect_privacy=False)
    else:
        resolved_user_getter = user_getter

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
        if user is None or not user.email or user.email in seen:
            continue
        seen.add(user.email)
        recipients.append(user.email)

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
    membership: Membership | None = None,
    days: int | None = None,
    force: bool = False,
    base_url: str | None = None,
    tz_name: str | None = None,
    extra_context: dict[str, str] | None = None,
) -> bool:
    """Queue a templated email via django-post-office.

    Passing an organization's paid *membership* attaches its renewal invoice and
    copies the business contact.

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
        committee_email = settings.MEMBERSHIP_COMMITTEE_EMAIL
        cc = [committee_email] if committee_email else None
        reply_to = [committee_email] if committee_email else None
        context = {
            **base_ctx,
            **membership_committee_email_context(),
            **system_email_context(),
            "organization_id": organization.pk,
            "organization_name": organization.name,
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

    attachments: dict[str, object] | None = None
    if membership is not None and invoice_eligible(membership):
        # Renewal reminders are only sent by the scheduled expiration jobs.
        invoice_parts = invoice_email_parts(
            membership,
            InvoiceTerm.renewal,
            recipient=address,
            cc=cc,
            actor_username=CUSTOS,
        )
        cc = invoice_parts.cc
        attachments = invoice_parts.attachments
        context |= invoice_parts.context

    queue_templated_email(
        recipients=[address],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=template_name,
        context=context,
        cc=cc,
        reply_to=reply_to,
        attachments=attachments,
    )

    return True
