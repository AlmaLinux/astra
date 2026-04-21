import logging
from typing import override

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the hourly operations: membership mirror validation."

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
            "operations_hourly: start force=%s dry_run=%s",
            force,
            dry_run,
        )

        for command_name, command_kwargs in (
            ("membership_mirror_validation", {"force": force, "dry_run": dry_run}),
        ):
            logger.info("operations_hourly: running %s", command_name)
            call_command(command_name, **command_kwargs)

        logger.info("operations_hourly: complete")