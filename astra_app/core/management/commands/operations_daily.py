import logging
from typing import override

from django.core.management.base import BaseCommand

from core.operations import run_operations

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the daily operations: expiration warnings, expired cleanup, "
        "team-leads sync, lifecycle cleanup, and invitation refresh."
    )

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Pass --force through to sub-commands.",
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
            "operations_daily: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        run_operations(
            name="operations_daily",
            jobs=(
                ("sponsorship_invoice_payment_details_check", {"dry_run": dry_run}),
                ("membership_expired_cleanup", {"force": force, "dry_run": dry_run}),
                ("membership_expiration_notifications", {"force": force, "dry_run": dry_run}),
                ("freeipa_membership_reconcile", {"report": True, "dry_run": dry_run}),
                ("freeipa_team_leads_sync", {"dry_run": dry_run}),
                ("selfservice_lifecycle_cleanup", {"dry_run": dry_run}),
                ("account_invitations_refresh", {}),
                ("send_mail_attachments_cleanup", {"dry_run": dry_run}),
            ),
        )

        logger.info("operations_daily: complete")