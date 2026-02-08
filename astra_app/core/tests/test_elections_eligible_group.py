
import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.backends import FreeIPAMisconfiguredError
from core.elections_eligibility import eligible_voters_from_memberships
from core.models import Election, Membership, MembershipType


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=90)
class ElectionEligibleGroupTests(TestCase):
    def test_eligible_voters_filtered_to_group_members(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Restricted election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.draft,
            eligible_group_cn="fas-restricted",
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )

        m1 = Membership.objects.create(target_username="alice", membership_type=voter_type, expires_at=None)
        m2 = Membership.objects.create(target_username="bob", membership_type=voter_type, expires_at=None)

        eligible_created_at = election.start_datetime - datetime.timedelta(days=120)
        Membership.objects.filter(pk=m1.pk).update(created_at=eligible_created_at)
        Membership.objects.filter(pk=m2.pk).update(created_at=eligible_created_at)

        group = SimpleNamespace(
            cn="fas-restricted",
            fas_group=True,
            description="",
            members=["alice"],
            member_groups=[],
        )

        def _get_group(*, cn: str, require_fresh: bool = False):
            if str(cn) != group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return group

        with patch("core.backends.get_freeipa_group_for_elections", side_effect=_get_group):
            eligible = eligible_voters_from_memberships(election=election)

        eligible_usernames = {v.username for v in eligible}
        self.assertEqual(eligible_usernames, {"alice"})

    def test_eligible_voters_include_nested_group_members(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Restricted election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.draft,
            eligible_group_cn="fas-root",
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )

        m1 = Membership.objects.create(target_username="alice", membership_type=voter_type, expires_at=None)
        m2 = Membership.objects.create(target_username="bob", membership_type=voter_type, expires_at=None)

        eligible_created_at = election.start_datetime - datetime.timedelta(days=120)
        Membership.objects.filter(pk=m1.pk).update(created_at=eligible_created_at)
        Membership.objects.filter(pk=m2.pk).update(created_at=eligible_created_at)

        root = SimpleNamespace(
            cn="fas-root",
            fas_group=True,
            description="",
            members=["alice"],
            member_groups=["fas-child"],
        )
        child = SimpleNamespace(
            cn="fas-child",
            fas_group=True,
            description="",
            members=["bob"],
            member_groups=[],
        )

        def _get_group(*, cn: str, require_fresh: bool = False):
            if cn == "fas-root":
                return root
            if cn == "fas-child":
                return child
            return None

        with patch("core.backends.get_freeipa_group_for_elections", side_effect=_get_group):
            eligible = eligible_voters_from_memberships(election=election)

        eligible_usernames = {v.username for v in eligible}
        self.assertEqual(eligible_usernames, {"alice", "bob"})
