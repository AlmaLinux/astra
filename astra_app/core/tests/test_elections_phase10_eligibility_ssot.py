import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from core import elections_eligibility
from core.elections_eligibility import (
    EligibilityFacts,
    eligible_voters_from_memberships,
    ineligible_voters_with_reasons,
)
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


class ElectionEligibilityFactsCacheTests(TestCase):
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
        cache.clear()

    def test_eligibility_facts_cache_hits_for_same_election_state(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 3, 1, 12, 0, 0), datetime.UTC)
        election = Election.objects.create(
            name="Eligibility cache election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=30),
            number_of_seats=1,
            status=Election.Status.open,
        )

        facts = {
            "alice": EligibilityFacts(
                weight=1,
                term_start_at=now - datetime.timedelta(days=120),
                has_any_vote_eligible=True,
                has_active_vote_eligible_at_reference=True,
            )
        }

        with patch(
            "core.elections_eligibility._compute_eligibility_facts_by_username",
            return_value=facts,
        ) as compute_mock:
            first = elections_eligibility._eligibility_facts_by_username(election=election)
            second = elections_eligibility._eligibility_facts_by_username(election=election)

        self.assertEqual(compute_mock.call_count, 1)
        self.assertEqual(first, second)

    def test_eligibility_facts_cache_invalidates_previous_state_on_transition(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 3, 1, 12, 0, 0), datetime.UTC)
        election = Election.objects.create(
            name="Eligibility cache transition election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=30),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        draft_facts = {
            "alice": EligibilityFacts(
                weight=1,
                term_start_at=now - datetime.timedelta(days=120),
                has_any_vote_eligible=True,
                has_active_vote_eligible_at_reference=True,
            )
        }
        open_facts = {
            "alice": EligibilityFacts(
                weight=2,
                term_start_at=now - datetime.timedelta(days=120),
                has_any_vote_eligible=True,
                has_active_vote_eligible_at_reference=True,
            )
        }

        with patch(
            "core.elections_eligibility._compute_eligibility_facts_by_username",
            side_effect=[draft_facts, open_facts],
        ) as compute_mock:
            elections_eligibility._eligibility_facts_by_username(election=election)

            draft_key = elections_eligibility._eligibility_facts_cache_key(
                election_id=election.id,
                election_state=Election.Status.draft,
            )
            self.assertIsNotNone(cache.get(draft_key))

            election.status = Election.Status.open
            election.save(update_fields=["status"])

            elections_eligibility._eligibility_facts_by_username(election=election)

            open_key = elections_eligibility._eligibility_facts_cache_key(
                election_id=election.id,
                election_state=Election.Status.open,
            )

        self.assertEqual(compute_mock.call_count, 2)
        self.assertIsNone(cache.get(draft_key))
        self.assertIsNotNone(cache.get(open_key))

    def test_eligibility_facts_cache_does_not_leak_across_elections(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 3, 1, 12, 0, 0), datetime.UTC)
        election_a = Election.objects.create(
            name="Eligibility cache election A",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=30),
            number_of_seats=1,
            status=Election.Status.open,
        )
        election_b = Election.objects.create(
            name="Eligibility cache election B",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=30),
            number_of_seats=1,
            status=Election.Status.open,
        )

        facts_a = {
            "alice": EligibilityFacts(
                weight=1,
                term_start_at=now - datetime.timedelta(days=120),
                has_any_vote_eligible=True,
                has_active_vote_eligible_at_reference=True,
            )
        }
        facts_b = {
            "bob": EligibilityFacts(
                weight=2,
                term_start_at=now - datetime.timedelta(days=120),
                has_any_vote_eligible=True,
                has_active_vote_eligible_at_reference=True,
            )
        }

        with patch(
            "core.elections_eligibility._compute_eligibility_facts_by_username",
            side_effect=[facts_a, facts_b],
        ) as compute_mock:
            first = elections_eligibility._eligibility_facts_by_username(election=election_a)
            second = elections_eligibility._eligibility_facts_by_username(election=election_b)

            key_a = elections_eligibility._eligibility_facts_cache_key(
                election_id=election_a.id,
                election_state=Election.Status.open,
            )
            key_b = elections_eligibility._eligibility_facts_cache_key(
                election_id=election_b.id,
                election_state=Election.Status.open,
            )

        self.assertEqual(compute_mock.call_count, 2)
        self.assertEqual(first, facts_a)
        self.assertEqual(second, facts_b)
        self.assertNotEqual(key_a, key_b)
        self.assertIsNotNone(cache.get(key_a))
        self.assertIsNotNone(cache.get(key_b))
