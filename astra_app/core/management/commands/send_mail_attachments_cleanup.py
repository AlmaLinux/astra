import logging
from typing import override

from django.core.management.base import BaseCommand

from core.mail_attachments import delete_stale_mail_attachment_uploads

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete Send Mail attachment uploads that were never sent and can no longer be."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count stale uploads without deleting them.",
        )

    @override
    def handle(self, *args, **options) -> None:
        dry_run: bool = bool(options.get("dry_run"))
        stale = delete_stale_mail_attachment_uploads(dry_run=dry_run)
        prefix = "[dry-run] would delete" if dry_run else "deleted"
        logger.info("send_mail_attachments_cleanup: %s %s stale upload(s)", prefix, stale)
