from __future__ import annotations

import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from core.backends import FreeIPAGroup, FreeIPAUser
from core.country_codes import (
    country_code_status_from_user_data,
    country_name_from_code,
    embargoed_country_codes_from_settings,
)
from core.email_context import membership_committee_email_context
from core.models import FreeIPAPermissionGrant, Membership
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
            Membership.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
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

            status = country_code_status_from_user_data(user._user_data)
            if not status.is_valid or not status.code:
                continue
            code = status.code
            if code not in embargoed_codes:
                continue

            full_name = str(user.full_name or "").strip()
            embargoed_members.append(
                {
                    "username": uname,
                    "full_name": full_name or uname,
                    "country_code": code,
                    "country_name": country_name_from_code(code),
                }
            )

        if not embargoed_members:
            self.stdout.write("No active members from embargoed countries.")
            return

        grants = list(FreeIPAPermissionGrant.objects.filter(permission=ASTRA_ADD_MEMBERSHIP))
        if not grants:
            raise CommandError(f"No FreeIPA grants exist for permission: {ASTRA_ADD_MEMBERSHIP}")

        direct_usernames: list[str] = []
        group_names: list[str] = []
        for grant in grants:
            if grant.principal_type == FreeIPAPermissionGrant.PrincipalType.user:
                direct_usernames.append(grant.principal_name)
            elif grant.principal_type == FreeIPAPermissionGrant.PrincipalType.group:
                group_names.append(grant.principal_name)

        recipients: list[str] = []
        seen: set[str] = set()

        expanded_usernames: list[str] = [*direct_usernames]
        for group_name in group_names:
            group = FreeIPAGroup.get(group_name)
            if group is None:
                raise CommandError(
                    f"Unable to load FreeIPA group referenced by permission grant: {group_name}"
                )
            expanded_usernames.extend(list(group.members))

        for username in expanded_usernames:
            user = FreeIPAUser.get(username)
            if user is None or not user.email:
                continue
            addr = str(user.email or "").strip()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            recipients.append(addr)

        if not recipients:
            raise CommandError(f"No email addresses found for any principals with {ASTRA_ADD_MEMBERSHIP}")

        if not force:
            from post_office.models import Email

            today = timezone.localdate()
            already_sent = Email.objects.filter(
                template__name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME,
                created__date=today,
            ).exists()
            if already_sent:
                if dry_run:
                    self.stdout.write("[dry-run] Would skip; email already queued today.")
                else:
                    self.stdout.write("Skipped; email already queued today.")
                return

        embargoed_members.sort(key=lambda row: (row.get("country_code") or "", row.get("username") or ""))
        recipients.sort()

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
