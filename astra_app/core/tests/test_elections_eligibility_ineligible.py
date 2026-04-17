"""Tests for ineligible_voters_with_reasons() in elections_eligibility."""

import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.elections_eligibility import ineligible_voters_with_reasons
from core.models import Election, Membership, MembershipType, Organization
from core.tests.utils_test_data import ensure_core_categories


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
class IneligibleVotersWithReasonsTests(TestCase):
    """Verify that ineligible_voters_with_reasons produces correct reason data."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _make_election(self, *, status: str = Election.Status.open, start_offset_days: int = 10) -> Election:
        """Create a test election with predictable dates."""
        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
        return Election.objects.create(
            name="Test election",
            description="",
            start_datetime=now - datetime.timedelta(days=start_offset_days),
            end_datetime=now + datetime.timedelta(days=30),
            number_of_seats=1,
            status=status,
        )

    def _make_voter_type(self) -> MembershipType:
        return MembershipType.objects.create(
            code="voter-b2",
            name="Voter B2",
            votes=1,
            category_id="individual",
            enabled=True,
        )

    def _snapshot_user(self, username: str) -> object:
        return type("SnapshotUser", (), {"username": username})()

    def test_no_membership_reason(self) -> None:
        """User in electorate with no membership rows → reason='no_membership'."""
        election = self._make_election()

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[self._snapshot_user("alice"), self._snapshot_user("bob")],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        usernames = {v["username"] for v in result}
        self.assertIn("alice", usernames)
        alice = next(v for v in result if v["username"] == "alice")
        self.assertEqual(alice["reason"], "no_membership")

    def test_expired_membership_reason(self) -> None:
        """User with expired membership → reason='expired'."""
        election = self._make_election()
        voter_type = self._make_voter_type()

        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
        # Created 60 days ago (old enough) but expired 15 days ago (before election start)
        m = Membership.objects.create(
            target_username="charlie",
            membership_type=voter_type,
            expires_at=now - datetime.timedelta(days=15),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=60))

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[self._snapshot_user("charlie")],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["reason"], "expired")

    def test_too_new_reason_with_days_short(self) -> None:
        """User with membership younger than min age → reason='too_new' with days_short."""
        election = self._make_election()
        voter_type = self._make_voter_type()

        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
        # Created 10 days ago — too new (need 30 days minimum)
        m = Membership.objects.create(
            target_username="newbie",
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=10))

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[self._snapshot_user("newbie")],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        self.assertEqual(len(result), 1)
        entry = result[0]
        self.assertEqual(entry["reason"], "too_new")
        self.assertIsInstance(entry["days_short"], int)
        self.assertGreater(entry["days_short"], 0)

    def test_eligible_user_not_in_results(self) -> None:
        """Users who are eligible should NOT appear in ineligible list."""
        election = self._make_election()
        voter_type = self._make_voter_type()

        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
        # Created 60 days ago, not expired — should be eligible
        m = Membership.objects.create(
            target_username="eligible_user",
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=m.pk).update(created_at=now - datetime.timedelta(days=60))

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[self._snapshot_user("eligible_user")],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        self.assertEqual(len(result), 0)

    def test_group_cn_restricts_electorate(self) -> None:
        """When eligible_group_cn is set, only group members are considered."""
        election = Election.objects.create(
            name="Group election",
            description="",
            start_datetime=datetime.datetime(2026, 2, 19, 12, 0, 0, tzinfo=datetime.UTC),
            end_datetime=datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC),
            number_of_seats=1,
            status=Election.Status.open,
            eligible_group_cn="voters-group",
        )

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                side_effect=AssertionError("configured-group branch must not use the shared snapshot helper"),
                create=True,
            ),
            patch(
                "core.elections_eligibility._freeipa_group_recursive_member_usernames",
                return_value={"groupmember"},
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        # groupmember is in the electorate but has no membership
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["username"], "groupmember")
        self.assertEqual(result[0]["reason"], "no_membership")

    def test_org_sponsorship_counts_as_membership(self) -> None:
        """Org sponsorship should be recognized as a vote-bearing membership."""
        election = self._make_election()
        voter_type = MembershipType.objects.create(
            code="org-voter-b2",
            name="Org Voter B2",
            votes=1,
            category_id="sponsorship",
            enabled=True,
        )

        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
        org = Organization.objects.create(
            name="Test Org",
            representative="orgrep",
        )
        s = Membership.objects.create(
            target_organization=org,
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=s.pk).update(created_at=now - datetime.timedelta(days=60))

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[self._snapshot_user("orgrep")],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        # orgrep should be eligible (old enough, not expired) → not in ineligible list
        self.assertEqual(len(result), 0)

    def test_result_structure_has_required_keys(self) -> None:
        """Each ineligible voter entry must have all required keys."""
        election = self._make_election()

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[self._snapshot_user("testuser")],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        self.assertEqual(len(result), 1)
        entry = result[0]
        required_keys = {"username", "reason", "term_start_date", "election_start_date", "days_at_start", "days_short"}
        self.assertEqual(set(entry.keys()), required_keys)

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30, FREEIPA_FILTERED_USERNAMES=("skipme",))
    def test_blank_group_snapshot_preserves_reason_parity_and_sorted_lowercase_usernames(self) -> None:
        election = self._make_election()
        voter_type = self._make_voter_type()

        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)

        expired = Membership.objects.create(
            target_username="expired_user",
            membership_type=voter_type,
            expires_at=now - datetime.timedelta(days=15),
        )
        Membership.objects.filter(pk=expired.pk).update(created_at=now - datetime.timedelta(days=60))

        too_new = Membership.objects.create(
            target_username="too_new",
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=too_new.pk).update(created_at=now - datetime.timedelta(days=10))

        eligible = Membership.objects.create(
            target_username="eligible_user",
            membership_type=voter_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=eligible.pk).update(created_at=now - datetime.timedelta(days=60))

        with (
            patch(
                "core.elections_eligibility.snapshot_freeipa_users",
                return_value=[
                    self._snapshot_user("Too_New"),
                    self._snapshot_user("Alice"),
                    self._snapshot_user("Expired_User"),
                    self._snapshot_user("Eligible_User"),
                ],
                create=True,
            ),
            patch(
                "core.freeipa.user.FreeIPAUser.all",
                side_effect=AssertionError("blank-group election fallback must not use FreeIPAUser.all()"),
            ),
        ):
            result = ineligible_voters_with_reasons(election=election)

        self.assertEqual([entry["username"] for entry in result], ["alice", "expired_user", "too_new"])
        reasons = {str(entry["username"]): str(entry["reason"]) for entry in result}
        self.assertEqual(reasons, {
            "alice": "no_membership",
            "expired_user": "expired",
            "too_new": "too_new",
        })

