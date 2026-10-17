"""Shared runner for the scheduled operations_* commands."""

import logging
from argparse import ArgumentParser
from collections.abc import Mapping, Sequence
from typing import override

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.logging_extras import current_exception_log_fields

logger = logging.getLogger(__name__)

type OperationJobs = Sequence[tuple[str, Mapping[str, object]]]


def add_force_and_dry_run_arguments(
    parser: ArgumentParser, *, force_help: str = "Send even if an email was already queued today."
) -> None:
    parser.add_argument("--force", action="store_true", help=force_help)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without mutating data or sending email.",
    )


def run_operations(*, name: str, jobs: OperationJobs) -> None:
    """Run each job's management command, isolating failures.

    A failing job is logged and the rest still run, since the jobs are independent.
    If any failed, the run ends with a CommandError naming them, so the scheduler
    still reports the run as failed.
    """
    failed: list[str] = []
    for command_name, command_kwargs in jobs:
        logger.info("%s: running %s", name, command_name)
        try:
            call_command(command_name, **command_kwargs)
        except Exception:
            logger.exception("%s: %s failed", name, command_name, extra=current_exception_log_fields())
            failed.append(command_name)
    if failed:
        raise CommandError(f"{name}: failed jobs: {', '.join(failed)}")


class OperationsCommand(BaseCommand):
    """A scheduled command that runs the sub-commands listed by jobs()."""

    def jobs(self, *, force: bool, dry_run: bool) -> OperationJobs:
        raise NotImplementedError

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        add_force_and_dry_run_arguments(parser, force_help="Pass --force through to sub-commands.")

    @override
    def handle(self, *args: object, **options: object) -> None:
        name = type(self).__module__.rpartition(".")[2]
        command_logger = logging.getLogger(type(self).__module__)
        force = bool(options["force"])
        dry_run = bool(options["dry_run"])
        command_logger.info("%s: start force=%s dry_run=%s", name, force, dry_run)
        run_operations(name=name, jobs=self.jobs(force=force, dry_run=dry_run))
        command_logger.info("%s: complete", name)
