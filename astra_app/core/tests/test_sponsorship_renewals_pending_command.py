import datetime
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core import signals as astra_signals
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Organization
from core.tests.utils_test_data import ensure_core_categories


class SponsorshipRenewalsPendingCommandTests(TestCase):
    def setUp(self) -> None:
        ensure_core_categories()
        self.gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={"name": "Gold Sponsor", "group_cn": "almalinux-gold", "category_id": "sponsorship", "enabled": True},
        )
        self.now = timezone.now()

    def _sponsorship(
        self, name: str, *, term_ended_days_ago: int, request_status: MembershipRequest.Status | None
    ) -> tuple[Membership, MembershipRequest | None]:
        """A sponsorship whose logged term ended *term_ended_days_ago*, held open by the 1-day grace."""
        organization = Organization.objects.create(name=name)
        MembershipLog.objects.create(
            actor_username="committee",
            target_organization=organization,
            membership_type=self.gold,
            action=MembershipLog.Action.approved,
            expires_at=self.now - datetime.timedelta(days=term_ended_days_ago),
        )
        # The grace hold moves only the membership's expiry and writes no log.
        membership = Membership.objects.create(
            target_organization=organization,
            membership_type=self.gold,
            expires_at=self.now + datetime.timedelta(days=1),
        )
        membership_request = (
            None
            if request_status is None
            else MembershipRequest.objects.create(
                requested_organization=organization, membership_type=self.gold, status=request_status
            )
        )
        return membership, membership_request

    def _run(self, *args: str) -> Mock:
        with patch("core.signals.organization_membership_renewals_pending.send", autospec=True) as send_mock:
            call_command("sponsorship_renewals_pending", *args)
        return send_mock

    def test_posts_held_renewals_most_overdue_first(self) -> None:
        recent, recent_request = self._sponsorship(
            "Recent", term_ended_days_ago=9, request_status=MembershipRequest.Status.pending
        )
        oldest, oldest_request = self._sponsorship(
            "Oldest", term_ended_days_ago=18, request_status=MembershipRequest.Status.on_hold
        )
        # Renewing before its term ended: nothing overdue yet.
        self._sponsorship("Early", term_ended_days_ago=-5, request_status=MembershipRequest.Status.pending)
        # Overdue without a renewal request is the expired-cleanup's to report, not this digest's.
        self._sponsorship("No Request", term_ended_days_ago=3, request_status=None)

        send_mock = self._run()

        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs["sender"], astra_signals.MembershipExpirationCommand)
        self.assertEqual(kwargs["count"], 2)
        self.assertEqual(
            [(renewal.membership, renewal.membership_request, renewal.term_ended_at) for renewal in kwargs["renewals"]],
            [
                (oldest, oldest_request, self.now - datetime.timedelta(days=18)),
                (recent, recent_request, self.now - datetime.timedelta(days=9)),
            ],
        )

    def test_does_not_post_when_nothing_is_overdue_or_on_dry_run(self) -> None:
        self._sponsorship("Early", term_ended_days_ago=-5, request_status=MembershipRequest.Status.pending)
        self._run().assert_not_called()

        self._sponsorship("Overdue", term_ended_days_ago=4, request_status=MembershipRequest.Status.pending)
        self._run("--dry-run").assert_not_called()
