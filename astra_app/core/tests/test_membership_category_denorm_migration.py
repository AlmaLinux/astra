from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from core.models import Membership, MembershipType, MembershipTypeCategory, Organization

MIGRATION_MODULE = "core.migrations.0084_repair_validate_membership_category_denorm"


class MembershipCategoryDenormMigrationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={"is_individual": False, "is_organization": True, "sort_order": 0},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={"is_individual": True, "is_organization": True, "sort_order": 1},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 2},
        )

    def test_validate_no_effective_category_duplicates_allows_unique_org_categories(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror-org",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror-org",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(target_organization=org, membership_type_id="gold")
        Membership.objects.create(target_organization=org, membership_type_id="mirror-org")

        migration = import_module(MIGRATION_MODULE)
        migration.validate_no_effective_category_duplicates(django_apps, None)

    def test_validate_no_effective_category_duplicates_fails_for_duplicates(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror-org",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror-org",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Duplicate Org", representative="bob")
        Membership.objects.create(target_organization=org, membership_type_id="gold")
        Membership.objects.create(target_organization=org, membership_type_id="mirror-org")

        MembershipType.objects.filter(code="mirror-org").update(category_id="sponsorship")

        migration = import_module(MIGRATION_MODULE)
        with self.assertRaisesRegex(RuntimeError, "duplicate org memberships"):
            migration.validate_no_effective_category_duplicates(django_apps, None)

    def test_validate_no_effective_category_duplicates_is_idempotent(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror-org",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror-org",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Idempotent Org", representative="bob")
        Membership.objects.create(target_organization=org, membership_type_id="gold")
        Membership.objects.create(target_organization=org, membership_type_id="mirror-org")

        migration = import_module(MIGRATION_MODULE)
        migration.validate_no_effective_category_duplicates(django_apps, None)
        migration.validate_no_effective_category_duplicates(django_apps, None)

    def test_migration_0084_includes_schema_cleanup_ops(self) -> None:
        migration = import_module(MIGRATION_MODULE)

        remove_constraint_names = [
            op.name
            for op in migration.Migration.operations
            if op.__class__.__name__ == "RemoveConstraint" and op.model_name == "membership"
        ]
        remove_field_names = [
            op.name
            for op in migration.Migration.operations
            if op.__class__.__name__ == "RemoveField" and op.model_name == "membership"
        ]

        self.assertIn("uniq_membership_org_category", remove_constraint_names)
        self.assertIn("category", remove_field_names)

    def test_membership_model_no_longer_has_denormalized_category_field(self) -> None:
        self.assertNotIn("category", {field.name for field in Membership._meta.fields})
