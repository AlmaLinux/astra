import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.country_codes import (
    embargoed_country_codes_from_settings,
    embargoed_country_match_from_user_data,
)
from core.email_context import membership_committee_email_context
from core.membership_notifications import (
    already_sent_today,
    committee_recipient_emails_for_permission_graceful,
)
from core.models import Membership
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Notify the membership committee about active members in embargoed countries."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if an email was already queued today.",
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

        embargoed_codes = embargoed_country_codes_from_settings()
        if not embargoed_codes:
            self.stdout.write("No embargoed country codes configured.")
            return

        now = timezone.now()
        memberships = (
            Membership.objects.active(at=now)
            .order_by("target_username")
            .values_list("target_username", flat=True)
        )

        embargoed_members: list[dict[str, str]] = []
        seen_usernames: set[str] = set()
        for username in memberships:
            uname = str(username or "").strip()
            if not uname or uname in seen_usernames:
                continue
            seen_usernames.add(uname)

            user = FreeIPAUser.get(uname)
            if user is None:
                continue

            embargoed_match = embargoed_country_match_from_user_data(
                user_data=user._user_data,
                embargoed_codes=embargoed_codes,
            )
            if embargoed_match is None:
                continue

            full_name = str(user.full_name or "").strip()
            country_name = embargoed_match.label.rsplit(" (", maxsplit=1)[0]
            embargoed_members.append(
                {
                    "username": uname,
                    "full_name": full_name or uname,
                    "country_code": embargoed_match.code,
                    "country_name": country_name,
                }
            )

        if not embargoed_members:
            self.stdout.write("No active members from embargoed countries.")
            return

        recipients, recipient_warnings = committee_recipient_emails_for_permission_graceful(
            permission=ASTRA_ADD_MEMBERSHIP,
        )
        for warning in recipient_warnings:
            self.stderr.write(f"Warning: {warning}")
        if not recipients:
            if dry_run:
                self.stdout.write("[dry-run] Would skip; no recipients resolved.")
            else:
                self.stdout.write("Skipped; no recipients resolved.")
            return

        if not force:
            already_sent = already_sent_today(
                template_name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME,
            )
            if already_sent:
                if dry_run:
                    self.stdout.write("[dry-run] Would skip; email already queued today.")
                else:
                    self.stdout.write("Skipped; email already queued today.")
                return

        embargoed_members.sort(key=lambda row: (row.get("country_code") or "", row.get("username") or ""))

        if dry_run:
            self.stdout.write(
                "[dry-run] Would queue 1 email to "
                f"{len(recipients)} recipient(s): {', '.join(recipients)}."
            )
            self.stdout.write(
                f"[dry-run] Would include {len(embargoed_members)} embargoed member(s)."
            )
            return

        queue_templated_email(
            recipients=recipients,
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME,
            context={
                **membership_committee_email_context(),
                "embargoed_count": len(embargoed_members),
                "embargoed_members": embargoed_members,
            },
            reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
        )

        self.stdout.write(f"Queued 1 email to {len(recipients)} recipient(s).")
