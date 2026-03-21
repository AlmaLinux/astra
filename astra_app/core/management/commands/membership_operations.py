import logging
from typing import override

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the membership cron operations: expiration warnings, expired cleanup, "
        "committee pending-request notifications, and embargoed-members notifications."
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
            "membership_operations: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        for command_name, command_kwargs in (
            ("membership_expired_cleanup", {"force": force, "dry_run": dry_run}),
            ("membership_expiration_notifications", {"force": force, "dry_run": dry_run}),
            ("freeipa_membership_reconcile", {"report": True, "dry_run": dry_run}),
            ("membership_pending_requests", {"force": force, "dry_run": dry_run}),
            ("membership_embargoed_members", {"force": force, "dry_run": dry_run}),
            ("membership_mirror_validation", {"force": force, "dry_run": dry_run}),
            ("selfservice_lifecycle_cleanup", {"dry_run": dry_run}),
            ("account_invitations_refresh", {}),
        ):
            logger.info("membership_operations: running %s", command_name)
            call_command(command_name, **command_kwargs)

        logger.info("membership_operations: complete")
