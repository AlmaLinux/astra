import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.backends import FreeIPAUser
from core.membership import FreeIPACallerMode, FreeIPAGroupRemovalOutcome, FreeIPAMissingUserPolicy
from core.membership_request_workflow import approve_membership_request
from core.models import Membership, MembershipRequest, MembershipType, MembershipTypeCategory, Organization


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
