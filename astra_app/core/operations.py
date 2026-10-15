"""Shared runner for the scheduled operations_* commands."""

import logging
from collections.abc import Mapping, Sequence

from django.core.management import call_command
from django.core.management.base import CommandError

from core.logging_extras import current_exception_log_fields

logger = logging.getLogger(__name__)


def run_operations(*, name: str, jobs: Sequence[tuple[str, Mapping[str, object]]]) -> None:
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
