import logging
from typing import override

from django.core.management.base import BaseCommand

from core.operations import run_operations

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the weekly operations: committee pending-request notifications "
        "and embargoed-members notifications."
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
            "operations_weekly: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        run_operations(
            name="operations_weekly",
            jobs=(
                ("membership_pending_requests", {"force": force, "dry_run": dry_run}),
                ("membership_embargoed_members", {"force": force, "dry_run": dry_run}),
            ),
        )

        logger.info("operations_weekly: complete")
