from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from core.membership import (
    FreeIPACallerMode,
    FreeIPAMissingUserPolicy,
    FreeIPARepresentativeSyncError,
    FreeIPARepresentativeSyncJournal,
    FreeIPARepresentativeSyncResult,
)
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Note, Organization
from core.organization_representative_transition import (
    OrganizationRepresentativeTransitionResult,
    apply_organization_representative_transition,
)
from core.tests.utils_test_data import FAR_FUTURE, ensure_core_categories


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
        Membership.objects.create(expires_at=FAR_FUTURE, target_organization=organization, membership_type_id="gold")
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
                actor_username="committee",
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
        Membership.objects.create(expires_at=FAR_FUTURE, target_organization=organization, membership_type_id="gold")
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
                    actor_username="committee",
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
        Membership.objects.create(expires_at=FAR_FUTURE, target_organization=organization, membership_type_id="gold")
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
                    actor_username="committee",
                    persist_changes=persist_changes,
                )

        rollback_mock.assert_called_once_with(
            old_representative="bob",
            new_representative="alice",
            journal=journal,
        )
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "bob")

    def test_persistence_failure_reraises_integrity_error_after_transaction_safe_rollback(self) -> None:
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
        Organization.objects.create(name="Existing Rep Org", representative="alice")
        organization = Organization.objects.create(name="Rollback Probe Org", representative="bob")
        Membership.objects.create(expires_at=FAR_FUTURE, target_organization=organization, membership_type_id="gold")
        journal = FreeIPARepresentativeSyncJournal(
            targeted_group_cns=("almalinux-gold",),
            skipped_group_cns=(),
            old_removed_group_cns=("almalinux-gold",),
            new_added_group_cns=("almalinux-gold",),
        )
        rollback_probe_counts: list[int] = []

        def persist_changes(locked_organization: Organization) -> None:
            locked_organization.save(update_fields=["representative"])

        def rollback_probe(*, old_representative: str, new_representative: str, journal: FreeIPARepresentativeSyncJournal) -> None:
            self.assertEqual(old_representative, "bob")
            self.assertEqual(new_representative, "alice")
            self.assertEqual(journal.targeted_group_cns, ("almalinux-gold",))
            rollback_probe_counts.append(Organization.objects.count())

        with (
            patch(
                "core.organization_representative_transition.sync_organization_representative_groups",
                return_value=SimpleNamespace(journal=journal),
            ),
            patch(
                "core.organization_representative_transition.rollback_organization_representative_groups",
                side_effect=rollback_probe,
            ) as rollback_mock,
        ):
            with self.assertRaises(IntegrityError):
                apply_organization_representative_transition(
                    organization_id=organization.pk,
                    new_representative="alice",
                    caller_label="test",
                    actor_username="committee",
                    persist_changes=persist_changes,
                )

        rollback_mock.assert_called_once()
        self.assertEqual(len(rollback_probe_counts), 1)
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
                actor_username="committee",
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
        Membership.objects.create(expires_at=FAR_FUTURE, target_organization=organization, membership_type_id="gold")
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
                actor_username="committee",
                persist_changes=persist_changes,
            )

        self.assertFalse(sync_mock.called)
        self.assertEqual(callback_calls, [organization.pk])
        self.assertFalse(result.changed)
        self.assertTrue(result.had_active_groups)
        self.assertEqual(result.targeted_group_cns, ("almalinux-gold",))
        organization.refresh_from_db()
        self.assertEqual(organization.business_contact_name, "After")
        self.assertFalse(MembershipLog.objects.exists())

    def test_change_is_logged_and_noted_on_active_sponsorships_and_open_requests(self) -> None:
        from core.membership_request_workflow import create_approved_membership_request

        gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={"name": "Gold", "category_id": "sponsorship", "enabled": True, "group_cn": "almalinux-gold"},
        )
        mirror, _ = MembershipType.objects.update_or_create(
            code="mirror",
            defaults={"name": "Mirror", "category_id": "mirror", "enabled": True},
        )
        organization = Organization.objects.create(name="Logged Org", representative="bob")
        Membership.objects.create(expires_at=FAR_FUTURE, target_organization=organization, membership_type=gold)
        term_request = create_approved_membership_request(
            actor_username="committee",
            membership_type=gold,
            decided_at=timezone.now(),
            target_organization=organization,
        )
        open_request = MembershipRequest.objects.create(
            requested_organization=organization,
            membership_type=mirror,
            status=MembershipRequest.Status.pending,
        )
        # Decided requests of lapsed terms are history: nothing about them changed.
        MembershipRequest.objects.create(
            requested_organization=organization,
            membership_type=mirror,
            status=MembershipRequest.Status.rejected,
        )
        journal = FreeIPARepresentativeSyncJournal(
            targeted_group_cns=("almalinux-gold",),
            skipped_group_cns=(),
            old_removed_group_cns=("almalinux-gold",),
            new_added_group_cns=("almalinux-gold",),
        )

        with patch(
            "core.organization_representative_transition.sync_organization_representative_groups",
            return_value=SimpleNamespace(journal=journal),
        ):
            apply_organization_representative_transition(
                organization_id=organization.pk,
                new_representative="alice",
                caller_label="test",
                actor_username="committee",
                persist_changes=lambda locked: locked.save(update_fields=["representative"]),
            )

        self.assertCountEqual(
            MembershipLog.objects.values_list(
                "action", "actor_username", "target_organization_id", "membership_type_id", "membership_request_id",
                "old_representative", "new_representative",
            ),
            [
                ("representative_changed", "committee", organization.pk, "gold", term_request.pk, "bob", "alice"),
                ("representative_changed", "committee", organization.pk, "mirror", open_request.pk, "bob", "alice"),
            ],
        )
        self.assertCountEqual(
            Note.objects.values_list("membership_request_id", "username", "action"),
            [
                (request.pk, "committee", {"type": "representative_changed", "old": "bob", "new": "alice"})
                for request in (term_request, open_request)
            ],
        )
