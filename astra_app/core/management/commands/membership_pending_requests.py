import datetime
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAGroup, FreeIPAUser
from core.email_context import membership_committee_email_context
from core.models import FreeIPAPermissionGrant, MembershipRequest
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.templated_email import queue_templated_email


def _membership_requests_url(*, base_url: str) -> str:
    path = reverse("membership-requests")
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return path
    return f"{base}{path}"


class Command(BaseCommand):
    help = "Notify the membership committee when pending membership requests exist."

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

        pending_count = MembershipRequest.objects.filter(status=MembershipRequest.Status.pending).count()
        if pending_count <= 0:
            self.stdout.write("No pending membership requests.")
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
                raise CommandError(f"Unable to load FreeIPA group referenced by permission grant: {group_name}")
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
            template_name = settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME

            if today.weekday() == 0:
                already_sent = Email.objects.filter(
                    template__name=template_name,
                    created__date=today,
                ).exists()
            else:
                this_weeks_monday = today - datetime.timedelta(days=today.weekday())
                already_sent = Email.objects.filter(
                    template__name=template_name,
                    created__date__gte=this_weeks_monday,
                ).exists()
            if already_sent:
                if dry_run:
                    self.stdout.write("[dry-run] Would skip; email already queued this week.")
                else:
                    self.stdout.write("Skipped; email already queued this week.")
                return

        recipients.sort()
        if dry_run:
            self.stdout.write(
                "[dry-run] Would queue 1 email to "
                f"{len(recipients)} recipient(s): {', '.join(recipients)}."
            )
            return

        queue_templated_email(
            recipients=recipients,
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
            context={
                **membership_committee_email_context(),
                "pending_count": pending_count,
                "requests_url": _membership_requests_url(base_url=settings.PUBLIC_BASE_URL),
            },
            reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
        )

        self.stdout.write(f"Queued 1 email to {len(recipients)} recipient(s).")
