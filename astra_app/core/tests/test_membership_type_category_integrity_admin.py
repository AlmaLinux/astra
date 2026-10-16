from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Membership, MembershipType, MembershipTypeCategory, Organization
from core.tests.utils_test_data import FAR_FUTURE, ensure_core_categories


class MembershipTypeCategoryIntegrityAdminTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def test_category_cannot_disable_is_organization_if_org_memberships_exist(self) -> None:
        sponsorship_category = MembershipTypeCategory.objects.get(pk="sponsorship")
        self.assertTrue(sponsorship_category.is_organization)

        org_type = MembershipType.objects.create(
            code="org_sponsor",
            name="Org sponsor",
            votes=5,
            category=sponsorship_category,
            enabled=True,
        )
        org = Organization.objects.create(name="Acme", representative="rep1")
        Membership.objects.create(
            target_organization=org,
            membership_type=org_type,
            expires_at=FAR_FUTURE,
        )

        sponsorship_category.is_organization = False
        with self.assertRaises(ValidationError):
            sponsorship_category.full_clean()

    def test_category_cannot_disable_is_individual_if_user_memberships_exist(self) -> None:
        individual_category = MembershipTypeCategory.objects.get(pk="individual")
        self.assertTrue(individual_category.is_individual)

        user_type = MembershipType.objects.create(
            code="user_voter",
            name="User voter",
            votes=2,
            category=individual_category,
            enabled=True,
        )
        Membership.objects.create(
            target_username="alice",
            membership_type=user_type,
            expires_at=FAR_FUTURE,
        )

        individual_category.is_individual = False
        with self.assertRaises(ValidationError):
            individual_category.full_clean()


class MembershipTypeIntegrityAdminTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def test_membership_type_cannot_move_to_non_org_category_if_org_memberships_exist(self) -> None:
        sponsorship_category = MembershipTypeCategory.objects.get(pk="sponsorship")
        individual_category = MembershipTypeCategory.objects.get(pk="individual")

        org_type = MembershipType.objects.create(
            code="org_sponsor",
            name="Org sponsor",
            votes=5,
            category=sponsorship_category,
            enabled=True,
        )
        org = Organization.objects.create(name="Acme", representative="rep1")
        Membership.objects.create(
            target_organization=org,
            membership_type=org_type,
            expires_at=FAR_FUTURE,
        )

        org_type.category = individual_category
        with self.assertRaises(ValidationError):
            org_type.full_clean()

    def test_membership_type_cannot_move_to_non_individual_category_if_user_memberships_exist(self) -> None:
        sponsorship_category = MembershipTypeCategory.objects.get(pk="sponsorship")
        individual_category = MembershipTypeCategory.objects.get(pk="individual")

        user_type = MembershipType.objects.create(
            code="user_voter",
            name="User voter",
            votes=2,
            category=individual_category,
            enabled=True,
        )
        Membership.objects.create(
            target_username="alice",
            membership_type=user_type,
            expires_at=FAR_FUTURE,
        )

        user_type.category = sponsorship_category
        with self.assertRaises(ValidationError):
            user_type.full_clean()
