import datetime

from django.test import TestCase
from django.utils import timezone

from core.models import Membership, MembershipType
from core.tests.utils_test_data import ensure_core_categories


class MembershipActiveBoundaryTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        MembershipType.objects.update_or_create(
            code="boundary-individual",
            defaults={
                "name": "Boundary Individual",
                "group_cn": "almalinux-boundary-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 999,
            },
        )

    def test_active_uses_exclusive_expires_at_boundary(self) -> None:
        now = timezone.now()
        membership_type = MembershipType.objects.get(code="boundary-individual")

        expires_now = Membership.objects.create(
            target_username="expires-now",
            membership_type=membership_type,
            expires_at=now,
        )
        expired = Membership.objects.create(
            target_username="expired-past",
            membership_type=membership_type,
            expires_at=now - datetime.timedelta(seconds=1),
        )
        active = Membership.objects.create(
            target_username="active-future",
            membership_type=membership_type,
            expires_at=now + datetime.timedelta(seconds=1),
        )
        no_expiry = Membership.objects.create(
            target_username="no-expiry",
            membership_type=membership_type,
            expires_at=None,
        )

        active_ids = set(Membership.objects.active(at=now).values_list("id", flat=True))

        self.assertNotIn(expires_now.id, active_ids)
        self.assertNotIn(expired.id, active_ids)
        self.assertIn(active.id, active_ids)
        self.assertIn(no_expiry.id, active_ids)
