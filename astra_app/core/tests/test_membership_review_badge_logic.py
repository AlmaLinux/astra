
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test.client import RequestFactory
from django.utils import timezone

from core.context_processors import membership_review
from core.freeipa.user import FreeIPAUser
from core.models import AccountInvitation, FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP


class MembershipReviewBadgeLogicTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

    def test_context_processor_counts_pending_and_on_hold(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )
        AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        rf = RequestFactory()
        request = rf.get("/")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            request.user = reviewer
            ctx = membership_review(request)

        self.assertEqual(ctx["membership_requests_pending_count"], 0)
        self.assertEqual(ctx["membership_requests_on_hold_count"], 2)
        self.assertEqual(ctx["account_invitations_accepted_count"], 1)

    def test_context_processor_hides_counts_for_view_only_user(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            note="",
            invited_by_username="committee",
            accepted_at=timezone.now(),
        )

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        rf = RequestFactory()
        request = rf.get("/")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            request.user = viewer
            ctx = membership_review(request)

        self.assertTrue(ctx["membership_can_view"])
        self.assertFalse(ctx["membership_can_add"])
        self.assertNotIn("membership_requests_pending_count", ctx)
        self.assertNotIn("membership_requests_on_hold_count", ctx)
        self.assertNotIn("account_invitations_accepted_count", ctx)
