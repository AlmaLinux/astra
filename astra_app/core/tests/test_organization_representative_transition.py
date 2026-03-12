from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from core.membership import (
    FreeIPACallerMode,
    FreeIPAMissingUserPolicy,
    FreeIPARepresentativeSyncError,
    FreeIPARepresentativeSyncJournal,
    FreeIPARepresentativeSyncResult,
)
from core.models import Membership, MembershipType, Organization
from core.organization_representative_transition import (
    OrganizationRepresentativeTransitionResult,
    apply_organization_representative_transition,
)
from core.tests.utils_test_data import ensure_core_categories


class OrganizationRepresentativeTransitionTests(TestCase):
    def setUp(self) -> None:
        ensure_core_categories()

    def test_active_group_transition_syncs_and_returns_structured_result(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        organization = Organization.objects.create(name="Sync Org", representative="bob")
        Membership.objects.create(target_organization=organization, membership_type_id="gold")
        journal = FreeIPARepresentativeSyncJournal(
            targeted_group_cns=("almalinux-gold",),
            skipped_group_cns=(),
            old_removed_group_cns=("almalinux-gold",),
            new_added_group_cns=("almalinux-gold",),
        )
        callback_calls: list[int] = []

        def persist_changes(locked_organization: Organization) -> None:
            callback_calls.append(locked_organization.pk)
            locked_organization.save(update_fields=["representative"])

        with patch(
            "core.organization_representative_transition.sync_organization_representative_groups",
            return_value=SimpleNamespace(journal=journal),
        ) as sync_mock:
            result = apply_organization_representative_transition(
                organization_id=organization.pk,
                new_representative="alice",
                caller_label="test",
                persist_changes=persist_changes,
            )

        self.assertIsInstance(result, OrganizationRepresentativeTransitionResult)
        self.assertEqual(result.organization.pk, organization.pk)
        self.assertEqual(result.old_representative, "bob")
        self.assertEqual(result.new_representative, "alice")
        self.assertTrue(result.changed)
        self.assertTrue(result.had_active_groups)
        self.assertEqual(result.targeted_group_cns, ("almalinux-gold",))
        self.assertEqual(callback_calls, [organization.pk])
        sync_mock.assert_called_once_with(
            old_representative="bob",
            new_representative="alice",
            group_cns=("almalinux-gold",),
            caller_mode=FreeIPACallerMode.raise_on_error,
            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
        )
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "alice")

    def test_freeipa_sync_failure_aborts_before_persistence(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        organization = Organization.objects.create(name="Failure Org", representative="bob")
        Membership.objects.create(target_organization=organization, membership_type_id="gold")
        callback_calls: list[int] = []
        failure = FreeIPARepresentativeSyncError(
            FreeIPARepresentativeSyncResult(
                journal=FreeIPARepresentativeSyncJournal(
                    targeted_group_cns=("almalinux-gold",),
                    skipped_group_cns=(),
                    old_removed_group_cns=(),
                    new_added_group_cns=(),
                ),
                failed_group_cns=("almalinux-gold",),
                failure_details={"almalinux-gold": "boom"},
            )
        )

        def persist_changes(locked_organization: Organization) -> None:
            callback_calls.append(locked_organization.pk)
            locked_organization.save(update_fields=["representative"])

        with patch(
            "core.organization_representative_transition.sync_organization_representative_groups",
            side_effect=failure,
        ):
            with self.assertRaises(FreeIPARepresentativeSyncError):
                apply_organization_representative_transition(
                    organization_id=organization.pk,
                    new_representative="alice",
                    caller_label="test",
                    persist_changes=persist_changes,
                )

        self.assertEqual(callback_calls, [])
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "bob")

    def test_persistence_failure_rolls_back_synced_groups_and_reraises(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        organization = Organization.objects.create(name="Rollback Org", representative="bob")
        Membership.objects.create(target_organization=organization, membership_type_id="gold")
        journal = FreeIPARepresentativeSyncJournal(
            targeted_group_cns=("almalinux-gold",),
            skipped_group_cns=(),
            old_removed_group_cns=("almalinux-gold",),
            new_added_group_cns=("almalinux-gold",),
        )

        def persist_changes(locked_organization: Organization) -> None:
            locked_organization.save(update_fields=["representative"])
            raise IntegrityError("db exploded")

        with (
            patch(
                "core.organization_representative_transition.sync_organization_representative_groups",
                return_value=SimpleNamespace(journal=journal),
            ),
            patch(
                "core.organization_representative_transition.rollback_organization_representative_groups"
            ) as rollback_mock,
        ):
            with self.assertRaises(IntegrityError):
                apply_organization_representative_transition(
                    organization_id=organization.pk,
                    new_representative="alice",
                    caller_label="test",
                    persist_changes=persist_changes,
                )

        rollback_mock.assert_called_once_with(
            old_representative="bob",
            new_representative="alice",
            journal=journal,
        )
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "bob")

    def test_no_active_groups_skips_freeipa_sync(self) -> None:
        organization = Organization.objects.create(name="No Groups Org", representative="bob")
        callback_calls: list[int] = []

        def persist_changes(locked_organization: Organization) -> None:
            callback_calls.append(locked_organization.pk)
            locked_organization.save(update_fields=["representative"])

        with patch(
            "core.organization_representative_transition.sync_organization_representative_groups"
        ) as sync_mock:
            result = apply_organization_representative_transition(
                organization_id=organization.pk,
                new_representative="alice",
                caller_label="test",
                persist_changes=persist_changes,
            )

        self.assertFalse(sync_mock.called)
        self.assertEqual(callback_calls, [organization.pk])
        self.assertTrue(result.changed)
        self.assertFalse(result.had_active_groups)
        self.assertEqual(result.targeted_group_cns, ())
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "alice")

    def test_noop_transition_skips_freeipa_sync_and_still_runs_persistence(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        organization = Organization.objects.create(
            name="Noop Org",
            representative="bob",
            business_contact_name="Before",
        )
        Membership.objects.create(target_organization=organization, membership_type_id="gold")
        callback_calls: list[int] = []

        def persist_changes(locked_organization: Organization) -> None:
            callback_calls.append(locked_organization.pk)
            locked_organization.business_contact_name = "After"
            locked_organization.save(update_fields=["business_contact_name"])

        with patch(
            "core.organization_representative_transition.sync_organization_representative_groups"
        ) as sync_mock:
            result = apply_organization_representative_transition(
                organization_id=organization.pk,
                new_representative="bob",
                caller_label="test",
                persist_changes=persist_changes,
            )

        self.assertFalse(sync_mock.called)
        self.assertEqual(callback_calls, [organization.pk])
        self.assertFalse(result.changed)
        self.assertTrue(result.had_active_groups)
        self.assertEqual(result.targeted_group_cns, ("almalinux-gold",))
        organization.refresh_from_db()
        self.assertEqual(organization.business_contact_name, "After")
