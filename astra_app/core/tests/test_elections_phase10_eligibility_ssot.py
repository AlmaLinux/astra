import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.elections_eligibility import eligible_voters_from_memberships, ineligible_voters_with_reasons
from core.models import Election, Membership, MembershipType, MembershipTypeCategory


class ElectionsPhase10EligibilitySSOTTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )

    def test_eligible_and_ineligible_paths_share_fact_builder(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 3, 1, 12, 0, 0), datetime.UTC)
        election = Election.objects.create(
            name="Eligibility ssot",
            description="",
            start_datetime=now - datetime.timedelta(days=5),
            end_datetime=now + datetime.timedelta(days=30),
            number_of_seats=1,
            status=Election.Status.draft,
        )
        membership_type = MembershipType.objects.create(
            code="voter-ssot",
            name="Voter SSOT",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=40))

        with (
            patch("django.utils.timezone.now", return_value=now),
            patch("core.elections_eligibility._eligibility_facts_by_username") as facts_mock,
        ):
            facts_mock.return_value = {}
            eligible_voters_from_memberships(election=election)
            ineligible_voters_with_reasons(election=election)

        self.assertGreaterEqual(facts_mock.call_count, 2)
