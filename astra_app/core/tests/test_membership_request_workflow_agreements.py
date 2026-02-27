import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership import FreeIPACallerMode, FreeIPAGroupRemovalOutcome, FreeIPAMissingUserPolicy
from core.membership_request_workflow import approve_membership_request
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)


class MembershipRequestWorkflowAgreementTests(TestCase):
    def test_user_approval_requires_required_agreements(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=["coc"],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get") as get_mock,
        ):
            with self.assertRaises(ValidationError) as ctx:
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

        self.assertIn("coc", str(ctx.exception))
        get_mock.assert_not_called()
        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)

    def test_org_approval_requires_required_agreements(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_organization": True,
                "sort_order": 1,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        membership_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type,
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=["coc"],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get") as get_mock,
        ):
            with self.assertRaises(ValidationError) as ctx:
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

        self.assertIn("coc", str(ctx.exception))
        get_mock.assert_not_called()
        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)

    def test_org_approval_fails_when_old_group_removal_fails(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_organization": True,
                "sort_order": 1,
            },
        )
        membership_type_old, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_type_new, _ = MembershipType.objects.update_or_create(
            code="platinum",
            defaults={
                "name": "Platinum",
                "group_cn": "almalinux-platinum",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        membership_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type_new,
        )
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type_old,
            category=membership_type_old.category,
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=bob),
            patch(
                "core.membership_request_workflow.remove_organization_representative_from_group_if_present",
                return_value=FreeIPAGroupRemovalOutcome.failed,
            ) as remove_mock,
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            with self.assertRaisesRegex(Exception, "Failed to remove user from old group"):
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

        remove_mock.assert_called_once_with(
            representative_username="bob",
            group_cn="almalinux-gold",
            caller_mode=FreeIPACallerMode.raise_on_error,
            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
        )
        add_mock.assert_not_called()
        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)

    def test_user_approval_treats_already_member_error_as_idempotent_success(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(
                FreeIPAUser,
                "add_to_group",
                autospec=True,
                side_effect=RuntimeError("This entry is already a member"),
            ),
        ):
            approve_membership_request(
                membership_request=membership_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.approved)

    def test_org_approval_treats_not_member_remove_error_as_idempotent_success(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_organization": True,
                "sort_order": 1,
            },
        )
        membership_type_old, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_type_new, _ = MembershipType.objects.update_or_create(
            code="platinum",
            defaults={
                "name": "Platinum",
                "group_cn": "almalinux-platinum",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        membership_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type_new,
            status=MembershipRequest.Status.pending,
        )
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type_old,
            category=membership_type_old.category,
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["almalinux-platinum"],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=bob),
            patch(
                "core.membership_request_workflow.remove_organization_representative_from_group_if_present",
                side_effect=RuntimeError("This entry is not a member"),
            ),
            patch(
                "core.membership_request_workflow.sync_organization_representative_groups",
                autospec=True,
            ),
        ):
            approve_membership_request(
                membership_request=membership_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.approved)

    def test_user_approval_creates_membership_row_and_mutates_freeipa_group(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            approve_membership_request(
                membership_request=membership_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.approved)
        self.assertTrue(
            Membership.objects.filter(
                target_username="alice",
                membership_type=membership_type,
            ).exists()
        )
        add_mock.assert_called_once_with(alice, group_name="almalinux-individual")

    def test_org_approval_replaces_existing_membership_row_and_mutates_freeipa_groups(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_organization": True,
                "sort_order": 1,
            },
        )
        membership_type_old, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_type_new, _ = MembershipType.objects.update_or_create(
            code="platinum",
            defaults={
                "name": "Platinum",
                "group_cn": "almalinux-platinum",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type_old,
            category=membership_type_old.category,
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type_new,
            status=MembershipRequest.Status.pending,
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["almalinux-gold"],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=bob),
            patch(
                "core.membership_request_workflow.remove_organization_representative_from_group_if_present",
                return_value=FreeIPAGroupRemovalOutcome.removed,
            ) as remove_mock,
            patch("core.membership_request_workflow.sync_organization_representative_groups", autospec=True) as sync_mock,
        ):
            approve_membership_request(
                membership_request=membership_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.approved)
        self.assertFalse(
            Membership.objects.filter(
                target_organization=org,
                membership_type=membership_type_old,
            ).exists()
        )
        self.assertTrue(
            Membership.objects.filter(
                target_organization=org,
                membership_type=membership_type_new,
            ).exists()
        )
        remove_mock.assert_called_once_with(
            representative_username="bob",
            group_cn="almalinux-gold",
            caller_mode=FreeIPACallerMode.raise_on_error,
            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
        )
        sync_mock.assert_called_once_with(
            old_representative="",
            new_representative="bob",
            group_cns=("almalinux-platinum",),
            caller_mode=FreeIPACallerMode.raise_on_error,
            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
        )

    def test_direct_membership_log_create_does_not_apply_membership_side_effects(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type=membership_type,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self.assertFalse(
            Membership.objects.filter(
                target_username="alice",
                membership_type=membership_type,
            ).exists()
        )

    def test_apply_membership_log_side_effects_service_applies_approved_log(self) -> None:
        from core.membership_log_side_effects import apply_membership_log_side_effects

        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        log = MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type=membership_type,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        apply_membership_log_side_effects(log=log)

        self.assertTrue(
            Membership.objects.filter(
                target_username="alice",
                membership_type=membership_type,
            ).exists()
        )

    def test_approve_on_hold_membership_request_requires_on_hold_status(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )

        workflow_module = __import__("core.membership_request_workflow", fromlist=["approve_on_hold_membership_request"])

        with self.assertRaises(ValidationError):
            workflow_module.approve_on_hold_membership_request(
                request_id=membership_request.pk,
                actor_username="reviewer",
                justification="Override is justified.",
            )

    def test_approve_on_hold_membership_request_records_justification_note(self) -> None:
        from core.models import Note

        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now(),
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        workflow_module = __import__("core.membership_request_workflow", fromlist=["approve_on_hold_membership_request"])

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            workflow_module.approve_on_hold_membership_request(
                request_id=membership_request.pk,
                actor_username="reviewer",
                justification="Committee reviewed required evidence.",
                send_approved_email=False,
            )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.approved)
        self.assertIsNone(membership_request.on_hold_at)
        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                username="reviewer",
                action__type="on_hold_override_approved",
                action__actors_note="Committee reviewed required evidence.",
            ).exists()
        )
