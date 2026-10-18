
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import (
    Membership,
    MembershipLog,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)


class OrganizationStatusTests(TestCase):
    def test_status_follows_representative_even_for_queryset_updates(self) -> None:
        organization = Organization.objects.create(name="Acme")
        self.assertEqual(organization.status, Organization.Status.unclaimed)

        Organization.objects.filter(pk=organization.pk).update(representative="bob")

        self.assertEqual(Organization.objects.get(pk=organization.pk).status, Organization.Status.active)


class MembershipExpiryRequiredTests(TestCase):
    def setUp(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={"is_individual": True, "is_organization": False},
        )
        self.membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "category_id": "individual", "enabled": True},
        )

    def test_membership_without_expiry_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(target_username="alice", membership_type=self.membership_type)

    def test_term_actions_without_expiry_are_rejected(self) -> None:
        for action in (MembershipLog.Action.approved, MembershipLog.Action.expiry_changed):
            with self.subTest(action=action):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    MembershipLog.objects.create(
                        actor_username="reviewer",
                        target_username="alice",
                        membership_type=self.membership_type,
                        action=action,
                    )
