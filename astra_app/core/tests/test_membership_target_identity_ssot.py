from typing import Any, cast

from django.test import TestCase
from django.utils import timezone

from core.membership_log_side_effects import apply_membership_log_side_effects
from core.membership_targets import MembershipTargetIdentity, MembershipTargetKind
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)


class MembershipTargetIdentitySSOTTests(TestCase):
    def setUp(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={"is_individual": True, "is_organization": False},
        )
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={"is_individual": False, "is_organization": True},
        )
        MembershipTypeCategory.objects.update_or_create(
            name="mirror",
            defaults={"is_individual": True, "is_organization": True},
        )
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-individual",
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "sponsor-group",
            },
        )
        MembershipType.objects.update_or_create(
            code="platinum",
            defaults={
                "name": "Platinum",
                "category_id": "sponsorship",
                "sort_order": 3,
                "enabled": True,
                "group_cn": "sponsor-platinum",
            },
        )

    def test_identity_value_primitive_builds_model_specific_filters(self) -> None:
        user_identity = MembershipTargetIdentity.for_user("alice")

        self.assertEqual(user_identity.kind, MembershipTargetKind.user)
        self.assertEqual(user_identity.identifier, "alice")
        self.assertEqual(user_identity.for_membership_request_filter(), {"requested_username": "alice"})
        self.assertEqual(user_identity.for_membership_filter(), {"target_username": "alice"})
        self.assertEqual(user_identity.for_membership_log_filter(), {"target_username": "alice"})

        org_identity = MembershipTargetIdentity.for_organization(
            organization_identifier="42",
            organization_display_name="Acme",
        )

        self.assertEqual(org_identity.kind, MembershipTargetKind.organization)
        self.assertEqual(org_identity.identifier, "42")
        self.assertEqual(org_identity.organization_display_name, "Acme")
        self.assertEqual(
            org_identity.for_membership_request_filter(),
            {"requested_organization_id": 42},
        )
        self.assertEqual(
            org_identity.for_membership_filter(),
            {"target_organization_id": 42},
        )
        self.assertEqual(
            org_identity.for_membership_log_filter(),
            {"target_organization_id": 42},
        )

    def test_models_expose_shared_target_identity_accessor(self) -> None:
        organization = Organization.objects.create(name="Acme", representative="rep")
        membership_type = MembershipType.objects.get(code="gold")

        request_row = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type=membership_type,
        )
        request_row.refresh_from_db()
        request_identity = request_row.target_identity

        self.assertEqual(request_identity.kind, MembershipTargetKind.organization)
        self.assertEqual(request_identity.organization_identifier, str(organization.pk))
        self.assertEqual(request_identity.organization_display_name, "Acme")

        membership = Membership.objects.create(
            target_organization=organization,
            membership_type=membership_type,
        )
        membership_identity = membership.target_identity
        self.assertEqual(membership_identity.kind, MembershipTargetKind.organization)
        self.assertEqual(membership_identity.organization_identifier, str(organization.pk))
        self.assertEqual(membership_identity.organization_display_name, "Acme")

        log = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            membership_type=membership_type,
            target_organization=organization,
        )
        log_identity = log.target_identity
        self.assertEqual(log_identity.kind, MembershipTargetKind.organization)
        self.assertEqual(log_identity.organization_identifier, str(organization.pk))
        self.assertEqual(log_identity.organization_display_name, "Acme")

    def test_membershiplog_queryset_org_matching_ssot_handles_fk_code_and_mismatch(self) -> None:
        membership_type = MembershipType.objects.get(code="gold")
        primary_org = Organization.objects.create(name="Primary", representative="rep")
        secondary_org = Organization.objects.create(name="Secondary", representative="rep2")

        fk_match = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            membership_type=membership_type,
            target_organization=primary_org,
            target_organization_code="999",
        )
        code_match = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            membership_type=membership_type,
            target_organization_code=str(primary_org.pk),
            target_organization_name="Primary snapshot",
        )
        mismatch_match = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            membership_type=membership_type,
            target_organization=secondary_org,
            target_organization_code=str(primary_org.pk),
        )
        non_match = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            membership_type=membership_type,
            target_organization=secondary_org,
        )

        logs_manager = cast(Any, MembershipLog.objects)
        matched_ids = set(
            logs_manager.for_organization_identifier(primary_org.pk).values_list("pk", flat=True)
        )
        self.assertEqual(matched_ids, {fk_match.pk, code_match.pk, mismatch_match.pk})
        self.assertNotIn(non_match.pk, matched_ids)

    def test_membershiplog_queryset_org_matching_includes_snapshot_rows_for_nonexistent_org(self) -> None:
        membership_type = MembershipType.objects.get(code="gold")
        snapshot_only = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            membership_type=membership_type,
            target_organization_code="424242",
            target_organization_name="Deleted org",
        )

        logs_manager = cast(Any, MembershipLog.objects)
        ids = set(logs_manager.for_organization_identifier(424242).values_list("pk", flat=True))
        self.assertEqual(ids, {snapshot_only.pk})

    def test_replace_within_category_uses_effective_category_when_denorm_drifts(self) -> None:
        organization = Organization.objects.create(name="Acme", representative="rep")
        Membership.objects.create(
            target_organization=organization,
            membership_type_id="gold",
        )

        new, old_copy = Membership.replace_within_category(
            organization=organization,
            new_membership_type=MembershipType.objects.get(code="platinum"),
            expires_at=None,
        )

        self.assertIsNotNone(old_copy)
        assert old_copy is not None
        self.assertEqual(old_copy.membership_type_id, "gold")
        self.assertEqual(new.membership_type_id, "platinum")
        self.assertEqual(Membership.objects.filter(target_organization=organization).count(), 1)

    def test_apply_user_side_effects_updates_existing_membership(self) -> None:
        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
        )

        log = MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            action=MembershipLog.Action.expiry_changed,
            expires_at=timezone.now(),
        )

        apply_membership_log_side_effects(log=log)

        membership.refresh_from_db()
        self.assertEqual(membership.expires_at, log.expires_at)
