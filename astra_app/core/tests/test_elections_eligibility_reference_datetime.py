from __future__ import annotations

import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.elections_eligibility import eligible_vote_weight_for_username, eligible_voters_from_memberships
from core.models import Election, Membership, MembershipLog, MembershipType


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
class ElectionEligibilityReferenceDatetimeTests(TestCase):
    def test_draft_election_uses_max_start_and_now(self) -> None:
        now = datetime.datetime(2026, 1, 20, 12, 0, 0, tzinfo=datetime.UTC)
        start_dt = now - datetime.timedelta(days=10)

        election = Election.objects.create(
            name="Draft eligibility election",
            description="",
            start_datetime=start_dt,
            end_datetime=now + datetime.timedelta(days=10),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )

        membership = Membership.objects.create(
            target_username="alice",
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )

        # Created 36 days before 'now': eligible by now-based cutoff (now-30),
        # but would be ineligible by start-based cutoff (start-30 == now-40).
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=36))

        with patch("django.utils.timezone.now", autospec=True, return_value=now):
            eligible = eligible_voters_from_memberships(election=election)

        self.assertEqual([v.username for v in eligible], ["alice"])

    def test_started_election_uses_start_datetime(self) -> None:
        now = datetime.datetime(2026, 1, 20, 12, 0, 0, tzinfo=datetime.UTC)
        start_dt = now - datetime.timedelta(days=10)

        election = Election.objects.create(
            name="Started eligibility election",
            description="",
            start_datetime=start_dt,
            end_datetime=now + datetime.timedelta(days=10),
            number_of_seats=1,
            status=Election.Status.open,
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )

        membership = Membership.objects.create(
            target_username="alice",
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )

        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=36))

        with patch("django.utils.timezone.now", autospec=True, return_value=now):
            eligible = eligible_voters_from_memberships(election=election)
            weight = eligible_vote_weight_for_username(election=election, username="alice")

        self.assertEqual([v.username for v in eligible], [])
        self.assertEqual(weight, 0)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=40)
class ElectionEligibilityRenewalPreservesCreatedAtTests(TestCase):
    def test_uninterrupted_membership_renewal_does_not_break_election_eligibility(self) -> None:
        start_at = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        extend_at = datetime.datetime(2026, 2, 1, 12, 0, 0, tzinfo=datetime.UTC)

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            first_log = MembershipLog.create_for_approval_at(
                actor_username="reviewer",
                target_username="alice",
                membership_type=voter_type,
                approved_at=start_at,
                previous_expires_at=None,
                membership_request=None,
            )

        previous_expires_at = first_log.expires_at
        assert previous_expires_at is not None

        with patch("django.utils.timezone.now", autospec=True, return_value=extend_at):
            MembershipLog.create_for_approval_at(
                actor_username="reviewer",
                target_username="alice",
                membership_type=voter_type,
                approved_at=extend_at,
                previous_expires_at=previous_expires_at,
                membership_request=None,
            )

        membership = Membership.objects.get(target_username="alice", membership_type=voter_type)
        self.assertEqual(membership.created_at, start_at)

        # Eligible at Feb 10 by uninterrupted term start (Jan 1), but would be
        # ineligible if created_at were reset to the renewal timestamp (Feb 1).
        election_start = datetime.datetime(2026, 2, 10, 12, 0, 0, tzinfo=datetime.UTC)
        election = Election.objects.create(
            name="Renewal eligibility election",
            description="",
            start_datetime=election_start,
            end_datetime=election_start + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )

        weight = eligible_vote_weight_for_username(election=election, username="alice")
        eligible = eligible_voters_from_memberships(election=election)

        self.assertEqual(weight, 1)
        self.assertEqual([v.username for v in eligible], ["alice"])
