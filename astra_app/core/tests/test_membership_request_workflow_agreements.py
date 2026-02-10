
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.membership_request_workflow import approve_membership_request
from core.models import MembershipRequest, MembershipType, Organization


class MembershipRequestWorkflowAgreementTests(TestCase):
    def test_user_approval_requires_required_agreements(self) -> None:
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
