import logging
from typing import Any, override

from django.core.management.base import BaseCommand
from django.db import connection
from post_office.mail import get_queued
from post_office.management.commands.send_queued_mail import Command as PostOfficeCommand
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Log as PostOfficeLog

from core.post_office_alerts import emit_immediate_ses_send_failure_alerts

logger = logging.getLogger(__name__)

# Coordinates scheduled ECS runs so multiple invocations do not send the same queued email
# concurrently (e.g. if a prior run is still executing when the next minute triggers).
_LOCK_KEY_1 = 189402183
_LOCK_KEY_2 = 915734211


def _queued_email_ids() -> list[int]:
    return list(get_queued().values_list("id", flat=True))


def _emit_new_bulk_failure_alerts(previous_log_id: int) -> None:
    # django-post-office writes queued-send logs with bulk_create(), so post_save
    # never fires for the production failed-delivery path.
    emit_immediate_ses_send_failure_alerts(
        PostOfficeLog.objects.filter(
            pk__gt=previous_log_id,
            status=POST_OFFICE_STATUS.failed,
        )
        .select_related("email")
        .order_by("pk")
    )


def _run_delegate_and_emit_alerts(*args: Any, **options: Any) -> Any:
    queued_email_ids = _queued_email_ids()
    if not queued_email_ids:
        return 0

    previous_log_id = PostOfficeLog.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    result = PostOfficeCommand().handle(*args, **options)
    _emit_new_bulk_failure_alerts(previous_log_id)
    return result


class Command(BaseCommand):
    help = PostOfficeCommand.help

    @override
    def add_arguments(self, parser) -> None:
        PostOfficeCommand().add_arguments(parser)

    @override
    def handle(self, *args: Any, **options: Any) -> Any:
        options.setdefault("log_level", 2)
        if connection.vendor != "postgresql":
            return _run_delegate_and_emit_alerts(*args, **options)

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
            return _run_delegate_and_emit_alerts(*args, **options)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    [_LOCK_KEY_1, _LOCK_KEY_2],
                )
