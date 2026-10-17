import datetime
from typing import Any
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from core.elections_eligibility import eligible_voters_from_memberships
from core.membership_log_side_effects import apply_membership_log_side_effects
from core.models import Election, Membership, MembershipLog, MembershipType, Organization
from core.tests.utils_test_data import FAR_FUTURE, ensure_core_categories


class MembershipSideEffectsTests(TestCase):
    """Users and organizations share one rule set: a type change replaces the row, any other change edits it."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        for code, category_id, votes in (
            ("individual", "individual", 1),
            ("supporter", "individual", 1),
            ("mirror", "mirror", 0),
            ("silver", "sponsorship", 1),
            ("gold", "sponsorship", 2),
        ):
            MembershipType.objects.update_or_create(
                code=code,
                defaults={"name": code.title(), "category_id": category_id, "votes": votes, "enabled": True, "sort_order": 0},
            )
        self.organization = Organization.objects.create(name="Acme", representative="rep1")
        self.start = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        # (name, target fields, a type, another type in the same category)
        self.targets: tuple[tuple[str, dict[str, Any], str, str], ...] = (
            ("user", {"target_username": "alice"}, "individual", "supporter"),
            ("organization", {"target_organization": self.organization}, "silver", "gold"),
        )

    def _day(self, days: int) -> datetime.datetime:
        return self.start + datetime.timedelta(days=days)

    def _log(
        self,
        target: dict[str, Any],
        *,
        at: datetime.datetime,
        action: MembershipLog.Action,
        membership_type_id: str,
        expires_at: datetime.datetime,
        dues_usd: int | None = None,
    ) -> None:
        with patch("django.utils.timezone.now", return_value=at):
            log = MembershipLog.objects.create(
                actor_username="committee",
                membership_type_id=membership_type_id,
                action=action,
                expires_at=expires_at,
                dues_usd=dues_usd,
                **target,
            )
            apply_membership_log_side_effects(log=log)

    def test_renewal_expiry_and_dues_changes_keep_the_row_and_its_start_date(self) -> None:
        for name, target, membership_type_id, _other_type_id in self.targets:
            with self.subTest(target=name):
                self._log(
                    target, at=self.start, action=MembershipLog.Action.approved,
                    membership_type_id=membership_type_id, expires_at=self._day(365),
                )
                original = Membership.objects.get(**target)
                # Dues only exist for sponsorships.
                dues_usd = 500 if name == "organization" else None

                for action, day, expires_day, dues in (
                    (MembershipLog.Action.approved, 300, 730, None),
                    (MembershipLog.Action.expiry_changed, 310, 740, None),
                    (MembershipLog.Action.dues_changed, 320, 740, dues_usd),
                ):
                    self._log(
                        target, at=self._day(day), action=action,
                        membership_type_id=membership_type_id, expires_at=self._day(expires_day), dues_usd=dues,
                    )

                current = Membership.objects.get(**target)
                self.assertEqual(current.pk, original.pk)
                self.assertEqual(current.created_at, self.start)
                self.assertEqual(current.expires_at, self._day(740))
                self.assertEqual(current.dues_usd, dues_usd)

    def test_type_change_within_a_category_replaces_the_row_but_keeps_the_start_date(self) -> None:
        for name, target, old_type_id, new_type_id in self.targets:
            with self.subTest(target=name):
                self._log(
                    target, at=self.start, action=MembershipLog.Action.approved,
                    membership_type_id=old_type_id, expires_at=self._day(365),
                )
                original = Membership.objects.get(**target)

                self._log(
                    target, at=self._day(200), action=MembershipLog.Action.approved,
                    membership_type_id=new_type_id, expires_at=self._day(565),
                )

                current = Membership.objects.get(**target)
                self.assertNotEqual(current.pk, original.pk)
                self.assertEqual(current.membership_type_id, new_type_id)
                self.assertEqual(current.created_at, self.start)

    def test_approval_after_a_lapse_starts_a_new_term(self) -> None:
        for name, target, membership_type_id, _other_type_id in self.targets:
            with self.subTest(target=name):
                self._log(
                    target, at=self.start, action=MembershipLog.Action.approved,
                    membership_type_id=membership_type_id, expires_at=self._day(365),
                )
                self._log(
                    target, at=self._day(400), action=MembershipLog.Action.approved,
                    membership_type_id=membership_type_id, expires_at=self._day(765),
                )

                self.assertEqual(Membership.objects.get(**target).created_at, self._day(400))

    def test_users_hold_at_most_one_membership_per_category(self) -> None:
        membership = Membership.objects.create(target_username="alice", membership_type_id="individual", expires_at=FAR_FUTURE)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(target_username="alice", membership_type_id="supporter", expires_at=FAR_FUTURE)

        # Another category, or editing the existing row, is fine.
        Membership.objects.create(target_username="alice", membership_type_id="mirror", expires_at=FAR_FUTURE)
        membership.expires_at = FAR_FUTURE - datetime.timedelta(days=1)
        membership.save()

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=90)
    def test_type_change_during_an_election_keeps_vote_eligibility(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )
        for _name, target, old_type_id, new_type_id in self.targets:
            self._log(
                target, at=now - datetime.timedelta(days=120), action=MembershipLog.Action.approved,
                membership_type_id=old_type_id, expires_at=now + datetime.timedelta(days=245),
            )
            self._log(
                target, at=now, action=MembershipLog.Action.approved,
                membership_type_id=new_type_id, expires_at=now + datetime.timedelta(days=365),
            )

        eligible = {voter.username: voter.weight for voter in eligible_voters_from_memberships(election=election)}

        self.assertEqual(eligible, {"alice": 1, "rep1": 2})
