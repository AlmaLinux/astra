import datetime

from django.test import TestCase
from django.utils import timezone

from core.models import MembershipLog, MembershipRequest, MembershipType
from core.tests.test_membership_dues import MembershipDuesTestMixin


class LifecycleLogTermRequestTests(MembershipDuesTestMixin, TestCase):
    """Expiry changes and terminations are filed under the request that granted the term,
    so the audit log links them to the membership like every other event."""

    def test_sponsorship_expiry_changes_and_termination_use_the_term_request(self) -> None:
        membership = self._active_gold()
        term_request = MembershipRequest.objects.get(requested_organization=self.organization)
        # A pending renewal is not the term being changed.
        self._request(self.gold)

        expiry_log = MembershipLog.create_for_expiry_change(
            actor_username="committee",
            membership_type=self.gold,
            expires_at=membership.expires_at + datetime.timedelta(days=10),
            target_organization=self.organization,
        )
        termination_log = MembershipLog.create_for_termination(
            actor_username="committee",
            membership_type=self.gold,
            target_organization=self.organization,
        )

        self.assertEqual(expiry_log.membership_request, term_request)
        self.assertEqual(termination_log.membership_request, term_request)

    def test_individual_expiry_change_uses_the_term_request(self) -> None:
        from core.membership_request_workflow import create_approved_membership_request

        individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        term_request = create_approved_membership_request(
            actor_username="committee",
            membership_type=individual,
            decided_at=timezone.now(),
            target_username="alice",
        )
        MembershipLog.create_for_approval(
            actor_username="committee",
            membership_type=individual,
            target_username="alice",
            membership_request=term_request,
        )

        log = MembershipLog.create_for_expiry_change(
            actor_username="committee",
            membership_type=individual,
            expires_at=timezone.now() + datetime.timedelta(days=30),
            target_username="alice",
        )

        self.assertEqual(log.membership_request, term_request)
