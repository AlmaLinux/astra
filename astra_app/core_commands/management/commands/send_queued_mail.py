import logging
from typing import Any, override

from django.core.management.base import BaseCommand
from django.db import connection
from post_office.mail import get_queued
from post_office.management.commands.send_queued_mail import Command as PostOfficeCommand

logger = logging.getLogger(__name__)

# Coordinates scheduled ECS runs so multiple invocations do not send the same queued email
# concurrently (e.g. if a prior run is still executing when the next minute triggers).
_LOCK_KEY_1 = 189402183
_LOCK_KEY_2 = 915734211


class Command(BaseCommand):
    help = PostOfficeCommand.help

    @override
    def add_arguments(self, parser) -> None:
        PostOfficeCommand().add_arguments(parser)

    @override
    def handle(self, *args: Any, **options: Any) -> Any:
        options.setdefault("log_level", 2)
        if connection.vendor != "postgresql":
            if not get_queued().exists():
                return 0
            return PostOfficeCommand().handle(*args, **options)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                [_LOCK_KEY_1, _LOCK_KEY_2],
            )
            row = cursor.fetchone()
            lock_acquired = bool(row and row[0])

        if not lock_acquired:
            logger.info("send_queued_mail: previous run still active; skipping")
            return 0

        try:
            if not get_queued().exists():
                return 0
            return PostOfficeCommand().handle(*args, **options)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    [_LOCK_KEY_1, _LOCK_KEY_2],
                )
