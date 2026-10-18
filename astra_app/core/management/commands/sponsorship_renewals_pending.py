import logging
from typing import override

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import signals as astra_signals
from core.membership import PendingRenewal, last_term_log, memberships_with_open_request
from core.models import Membership

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Post the sponsorships kept past their term because their renewal request is still undecided."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Log the pending renewals without posting them.")

    @override
    def handle(self, *args, **options) -> None:
        dry_run: bool = bool(options.get("dry_run"))
        now = timezone.now()

        sponsorships = list(
            Membership.objects.active()
            .filter(target_organization__isnull=False)
            .select_related("membership_type", "target_organization")
        )
        with_open_request = memberships_with_open_request(sponsorships)

        renewals: list[PendingRenewal] = []
        for sponsorship in sponsorships:
            membership_request = with_open_request.get(sponsorship.pk)
            if membership_request is None:
                continue
            last_term = last_term_log(
                organization=sponsorship.target_organization,
                category_id=sponsorship.membership_type.category_id,
            )
            # Without a logged expiry there is no term end to measure the hold against.
            if last_term is None or last_term.expires_at is None or last_term.expires_at >= now:
                continue
            renewals.append(
                PendingRenewal(
                    membership=sponsorship,
                    membership_request=membership_request,
                    term_ended_at=last_term.expires_at,
                )
            )
        renewals.sort(key=lambda renewal: renewal.term_ended_at)

        if dry_run:
            logger.info("[dry-run] Would post %s pending sponsorship renewal(s).", len(renewals))
            return
        logger.info("Found %s pending sponsorship renewal(s).", len(renewals))
        if renewals:
            astra_signals.organization_membership_renewals_pending.send(
                sender=astra_signals.MembershipExpirationCommand,
                count=len(renewals),
                renewals=renewals,
            )
