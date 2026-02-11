import datetime

from django.test import TestCase
from django.utils import timezone

from core.models import Membership, MembershipType, Organization
from core.views_membership import _load_active_membership


class MembershipLoaderHelperTests(TestCase):
    def test_load_active_membership_for_user_returns_matching_membership(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=None,
        )

        membership_type = MembershipType.objects.get(code="individual")
        active = _load_active_membership(username="alice", membership_type=membership_type)

        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.pk, membership.pk)

    def test_load_active_membership_for_organization_ignores_expired(self) -> None:
        MembershipType.objects.update_or_create(
            code="sponsor",
            defaults={
                "name": "Sponsor",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="sponsor",
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        membership_type = MembershipType.objects.get(code="sponsor")
        active = _load_active_membership(organization=org, membership_type=membership_type)

        self.assertIsNone(active)
