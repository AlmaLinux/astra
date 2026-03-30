import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.email_context import membership_committee_email_context
from core.freeipa.user import FreeIPAUser
from core.membership import visible_committee_membership_requests
from core.membership_notifications import (
    committee_recipient_emails_for_permission_graceful,
    membership_requests_url,
    oldest_pending_membership_request_wait_time,
    would_queue_membership_pending_requests_notification,
)
from core.models import MembershipRequest
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Notify the Membership Committee when pending membership requests exist."

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
        today = timezone.localdate()

        all_freeipa_users = FreeIPAUser.all()
        live_users_by_username = {freeipa_user.username: freeipa_user for freeipa_user in all_freeipa_users if freeipa_user.username}

        pending_count = len(
            visible_committee_membership_requests(
                MembershipRequest.objects.select_related("requested_organization")
                .filter(status=MembershipRequest.Status.pending)
                .order_by("requested_at", "pk"),
                live_users_by_username=live_users_by_username,
            )
        )
        if pending_count <= 0:
            logger.info("No pending membership requests.")
            return

        logger.info(
            "membership_pending_requests: start pending_count=%s force=%s dry_run=%s",
            pending_count,
            force,
            dry_run,
        )

        recipients, recipient_warnings = committee_recipient_emails_for_permission_graceful(
            permission=ASTRA_ADD_MEMBERSHIP,
        )
        for warning in recipient_warnings:
            logger.warning("%s", warning)
        if not recipients:
            if dry_run:
                logger.info("[dry-run] Would skip; no recipients resolved.")
            else:
                logger.info("Skipped; no recipients resolved.")
            return

        if not would_queue_membership_pending_requests_notification(
            force=force,
            template_name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
            today=today,
        ):
            if dry_run:
                logger.info("[dry-run] Would skip; email already queued this week.")
            else:
                logger.info("Skipped; email already queued this week.")
            return

        oldest_wait_time = oldest_pending_membership_request_wait_time(
            live_users_by_username=live_users_by_username,
        )

        if dry_run:
            logger.info(
                "[dry-run] Would queue 1 email to %s recipient(s): %s.",
                len(recipients),
                ", ".join(recipients),
            )
            return

        context = {
            **membership_committee_email_context(),
            "pending_count": pending_count,
            "requests_url": membership_requests_url(base_url=settings.PUBLIC_BASE_URL),
        }
        if oldest_wait_time is not None:
            context["oldest_wait_time"] = oldest_wait_time

        queue_templated_email(
            recipients=recipients,
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
            context=context,
            reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
        )

        logger.info("Queued 1 email to %s recipient(s).", len(recipients))
