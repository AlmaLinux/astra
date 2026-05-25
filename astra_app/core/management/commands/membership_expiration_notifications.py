import datetime
import logging
import math
from collections.abc import Iterable
from typing import override
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import signals as astra_signals
from core.email_context import user_email_context_from_user
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import _first
from core.membership import get_expiring_memberships, membership_request_queryset
from core.membership_notifications import (
    organization_membership_request_url,
    organization_sponsor_notification_recipient_email,
    send_membership_notification,
    would_queue_membership_notification,
)
from core.models import Membership, MembershipRequest, MembershipType

logger = logging.getLogger(__name__)


def _open_membership_request_keys(
    *,
    requested_usernames: set[str],
    requested_organization_ids: set[int],
    membership_category_ids: set[str],
) -> tuple[set[tuple[str, str]], set[tuple[int, str]]]:
    if not membership_category_ids:
        return set(), set()

    open_requests = membership_request_queryset().filter(
        membership_type__category_id__in=membership_category_ids,
        status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
    )

    user_keys: set[tuple[str, str]] = set()
    if requested_usernames:
        user_keys = {
            (requested_username, membership_category_id)
            for requested_username, membership_category_id in open_requests.filter(
                requested_username__in=requested_usernames,
                requested_organization__isnull=True,
            ).values_list("requested_username", "membership_type__category_id")
        }

    organization_keys: set[tuple[int, str]] = set()
    if requested_organization_ids:
        organization_keys = {
            (requested_organization_id, membership_category_id)
            for requested_organization_id, membership_category_id in open_requests.filter(
                requested_organization_id__in=requested_organization_ids,
            ).values_list("requested_organization_id", "membership_type__category_id")
        }

    return user_keys, organization_keys


class Command(BaseCommand):
    help = "Send membership expiration warning emails via django-post-office."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if an email was already queued today.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without mutating data or sending email.",
        )

    @override
    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))
        dry_run: bool = bool(options.get("dry_run"))

        logger.info(
            "membership_expiration_notifications: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        now = timezone.now()
        today_utc = now.astimezone(datetime.UTC).date()

        NUMBER_OF_SCHEDULED_NOTIFICATIONS = 7
        schedule_divisors = (2**i for i in range(NUMBER_OF_SCHEDULED_NOTIFICATIONS))
        schedule_days = [
            math.floor(settings.MEMBERSHIP_EXPIRING_SOON_DAYS / divisor)
            for divisor in schedule_divisors
        ]

        max_schedule_days = max(schedule_days) if schedule_days else 0
        window_days = max_schedule_days + 1
        # The schedule uses day differences, so include the full cutoff date window.
        expiring_memberships = get_expiring_memberships(days=window_days)
        memberships: Iterable[Membership] = [
            membership
            for membership in expiring_memberships
            if str(membership.target_username or "").strip()
        ]
        sponsorships: Iterable[Membership] = [
            membership
            for membership in expiring_memberships
            if membership.target_organization is not None
        ]

        queued = 0
        skipped = 0
        membership_usernames: set[str] = {
            str(membership.target_username or "").strip()
            for membership in memberships
            if str(membership.target_username or "").strip()
        }
        sponsorship_organization_ids: set[int] = {
            int(membership.target_organization.pk)
            for membership in sponsorships
            if membership.target_organization is not None
        }
        membership_type_ids: set[str] = {
            str(membership.membership_type.code or "").strip()
            for membership in expiring_memberships
            if str(membership.membership_type.code or "").strip()
        }
        membership_category_by_type_id = {
            str(type_id): str(category_id)
            for type_id, category_id in MembershipType.objects.filter(code__in=membership_type_ids).values_list(
                "code",
                "category_id",
            )
        }
        membership_category_ids: set[str] = {
            category_id
            for category_id in membership_category_by_type_id.values()
            if category_id.strip()
        }
        open_request_keys, open_org_request_keys = _open_membership_request_keys(
            requested_usernames=membership_usernames,
            requested_organization_ids=sponsorship_organization_ids,
            membership_category_ids=membership_category_ids,
        )

        for membership in memberships:
            if not membership.expires_at:
                continue

            if membership.expires_at <= now:
                continue

            expires_on_utc = membership.expires_at.astimezone(datetime.UTC).date()
            days_until = (expires_on_utc - today_utc).days

            if days_until not in schedule_days:
                continue

            template = settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME

            membership_target_username = str(membership.target_username or "").strip()
            membership_category_id = str(
                membership_category_by_type_id.get(str(membership.membership_type.code or "").strip(), "") or ""
            ).strip()
            membership_request_key = (membership_target_username, membership_category_id)
            if membership_request_key in open_request_keys:
                if days_until == 1:
                    if dry_run:
                        logger.info(
                            "[dry-run] Would extend membership expiration for %s/%s by 1 day due to open membership request.",
                            membership_target_username,
                            membership_category_id,
                        )
                    else:
                        membership.expires_at = membership.expires_at + datetime.timedelta(days=1)
                        membership.save(update_fields=["expires_at"])
                        logger.info(
                            "Extended membership expiration for %s/%s by 1 day due to open membership request.",
                            membership_target_username,
                            membership_category_id,
                        )
                logger.info(
                    "Skipped %s for %s; open membership request exists for %s.",
                    template,
                    membership_target_username,
                    membership_category_id,
                )
                continue

            fu = FreeIPAUser.get(membership.target_username)
            if fu is None or not fu.email:
                continue

            tz_name = str(_first(fu._user_data, "fasTimezone", "") or "").strip() or "UTC"
            if dry_run:
                would_queue = would_queue_membership_notification(
                    force=force,
                    template_name=template,
                    recipient_email=fu.email,
                    membership_type=membership.membership_type,
                )

                if would_queue:
                    logger.info("[dry-run] Would queue %s to %s.", template, fu.email)
                    queued += 1
                else:
                    logger.info("[dry-run] Would skip %s to %s; already queued today.", template, fu.email)
                    skipped += 1
            else:
                did_queue = send_membership_notification(
                    recipient_email=fu.email,
                    membership_type=membership.membership_type,
                    template_name=template,
                    expires_at=membership.expires_at,
                    username=membership.target_username,
                    days=days_until,
                    force=force,
                    tz_name=tz_name,
                    extra_context=user_email_context_from_user(user=fu),
                )
                if did_queue:
                    queued += 1
                    logger.info("Queued %s to %s.", template, fu.email)
                else:
                    skipped += 1
                    logger.info("Skipped %s to %s; already queued today.", template, fu.email)

        for sponsorship in sponsorships:
            if not sponsorship.expires_at:
                continue

            if sponsorship.expires_at <= now:
                continue

            expires_on_utc = sponsorship.expires_at.astimezone(datetime.UTC).date()
            days_until = (expires_on_utc - today_utc).days

            if days_until not in schedule_days:
                continue

            template = settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME

            target_organization = sponsorship.target_organization
            if target_organization is None:
                continue

            organization_id = target_organization.pk
            sponsorship_category_id = str(
                membership_category_by_type_id.get(str(sponsorship.membership_type.code or "").strip(), "") or ""
            ).strip()
            sponsorship_request_key = (organization_id, sponsorship_category_id)
            if organization_id is not None and sponsorship_request_key in open_org_request_keys:
                if days_until == 1:
                    if dry_run:
                        logger.info(
                            "[dry-run] Would extend sponsorship expiration for org=%s/%s by 1 day due to open membership request.",
                            organization_id,
                            sponsorship_category_id,
                        )
                    else:
                        sponsorship.expires_at = sponsorship.expires_at + datetime.timedelta(days=1)
                        sponsorship.save(update_fields=["expires_at"])
                        logger.info(
                            "Extended sponsorship expiration for org=%s/%s by 1 day due to open membership request.",
                            organization_id,
                            sponsorship_category_id,
                        )
                logger.info(
                    "Skipped %s for org=%s; open membership request exists for %s.",
                    template,
                    organization_id,
                    sponsorship_category_id,
                )
                continue

            recipient_email, recipient_warning = organization_sponsor_notification_recipient_email(
                organization=target_organization,
                notification_kind="organization sponsorship expiring-soon",
            )
            if recipient_warning:
                logger.warning("%s", recipient_warning)
            if not recipient_email:
                continue

            extend_url = organization_membership_request_url(
                organization_id=organization_id,
                membership_type_code=sponsorship.membership_type.code,
                base_url=settings.PUBLIC_BASE_URL,
            )

            if dry_run:
                would_queue = would_queue_membership_notification(
                    force=force,
                    template_name=template,
                    recipient_email=recipient_email,
                    membership_type=sponsorship.membership_type,
                    organization=target_organization,
                )

                if would_queue:
                    logger.info("[dry-run] Would queue %s to %s.", template, recipient_email)
                    queued += 1
                else:
                    logger.info(
                        "[dry-run] Would skip %s to %s; already queued today.",
                        template,
                        recipient_email,
                    )
                    skipped += 1
            else:
                rep_timezone: str = "UTC"
                representative_username = str(target_organization.representative or "").strip()
                if representative_username:
                    rep_user = FreeIPAUser.get(representative_username)
                    if rep_user is not None:
                        candidate_tz = str(_first(rep_user._user_data, "fasTimezone", "") or "").strip()
                        if candidate_tz:
                            try:
                                ZoneInfo(candidate_tz)
                            except Exception:
                                rep_timezone = "UTC"
                            else:
                                rep_timezone = candidate_tz

                did_queue = send_membership_notification(
                    recipient_email=recipient_email,
                    membership_type=sponsorship.membership_type,
                    template_name=template,
                    expires_at=sponsorship.expires_at,
                    organization=target_organization,
                    days=days_until,
                    force=force,
                    tz_name=rep_timezone,
                    extra_context={
                        "extend_url": extend_url,
                    },
                )
                if did_queue:
                    queued += 1
                    logger.info("Queued %s to %s.", template, recipient_email)
                else:
                    skipped += 1
                    logger.info("Skipped %s to %s; already queued today.", template, recipient_email)

        summary = f"Queued {queued} email(s); skipped {skipped}."
        if dry_run:
            logger.info("[dry-run] %s", summary)
        else:
            logger.info(summary)
            if queued > 0:
                astra_signals.membership_expiring_soon.send(
                    sender=astra_signals.MembershipExpirationCommand,
                    count=queued,
                    membership_type="all",
                )
