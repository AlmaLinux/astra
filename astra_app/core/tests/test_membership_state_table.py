
import datetime

from django.test import TestCase
from django.utils import timezone

from core.membership_log_side_effects import apply_membership_log_side_effects
from core.tests.utils_test_data import ensure_core_categories


class MembershipStateTableTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _create_membership_log_with_side_effects(self, **kwargs):
        from core.models import MembershipLog

        log = MembershipLog.objects.create(**kwargs)
        apply_membership_log_side_effects(log=log)
        return log

    def test_membership_log_write_updates_membership_state(self) -> None:
        from core.membership import get_valid_memberships
        from core.models import Membership, MembershipLog, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        expires_at = timezone.now() + datetime.timedelta(days=30)
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=expires_at,
        )

        state = Membership.objects.get(target_username="alice", membership_type_id="individual")
        self.assertEqual(state.expires_at, expires_at)

        valid = get_valid_memberships(username="alice")
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0].membership_type_id, "individual")

    def test_termination_updates_membership_state_and_invalidates(self) -> None:
        from core.membership import get_valid_memberships
        from core.models import Membership, MembershipLog, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        now = timezone.now()
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=30),
        )
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.terminated,
            expires_at=now,
        )

        # Termination removes the current-state row entirely.
        with self.assertRaises(Membership.DoesNotExist):
            Membership.objects.get(target_username="alice", membership_type_id="individual")
        self.assertEqual(get_valid_memberships(username="alice"), [])
