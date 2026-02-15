from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand

from core.email_context import membership_committee_email_context
from core.membership_notifications import (
    committee_recipient_emails_for_permission_graceful,
    membership_requests_url,
    would_queue_membership_pending_requests_notification,
)
from core.models import MembershipRequest
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.templated_email import queue_templated_email


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

        pending_count = MembershipRequest.objects.filter(status=MembershipRequest.Status.pending).count()
        if pending_count <= 0:
            self.stdout.write("No pending membership requests.")
            return

        recipients, recipient_warnings = committee_recipient_emails_for_permission_graceful(
            permission=ASTRA_ADD_MEMBERSHIP,
        )
        for warning in recipient_warnings:
            self.stderr.write(f"Warning: {warning}")
        if not recipients:
            if dry_run:
                self.stdout.write("[dry-run] Would skip; no recipients resolved.")
            else:
                self.stdout.write("Skipped; no recipients resolved.")
            return

        if not would_queue_membership_pending_requests_notification(
            force=force,
            template_name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
        ):
            if dry_run:
                self.stdout.write("[dry-run] Would skip; email already queued this week.")
            else:
                self.stdout.write("Skipped; email already queued this week.")
            return

        if dry_run:
            self.stdout.write(
                "[dry-run] Would queue 1 email to "
                f"{len(recipients)} recipient(s): {', '.join(recipients)}."
            )
            return

        queue_templated_email(
            recipients=recipients,
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
            context={
                **membership_committee_email_context(),
                "pending_count": pending_count,
                "requests_url": membership_requests_url(base_url=settings.PUBLIC_BASE_URL),
            },
            reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
        )

        self.stdout.write(f"Queued 1 email to {len(recipients)} recipient(s).")
