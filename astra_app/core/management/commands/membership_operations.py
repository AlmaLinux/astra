from __future__ import annotations

from typing import override

from django.core.management import call_command
from django.core.management.base import BaseCommand


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

    @override
    def handle(self, *args, **options) -> None:
        force: bool = bool(options.get("force"))

        call_command("membership_expired_cleanup", force=force)
        call_command("membership_expiration_notifications", force=force)
        call_command("organization_sponsorship_expired_cleanup")
        call_command("freeipa_membership_reconcile", report=True)
        call_command("membership_pending_requests", force=force)
        call_command("membership_embargoed_members", force=force)
