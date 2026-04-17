
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test.client import RequestFactory
from django.utils import timezone

from core.context_processors import membership_review
from core.freeipa.user import FreeIPAUser
from core.membership import visible_committee_membership_requests
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
        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": []})

        rf = RequestFactory()
        request = rf.get("/")

        with (
            patch("core.context_processors.FreeIPAUser.all", side_effect=AssertionError("membership_review should use the shared lightweight username helper")),
            patch(
                "core.context_processors.FreeIPAUser.find_lightweight_by_usernames",
                create=True,
                return_value={alice.username: alice, bob.username: bob},
            ) as find_lightweight_mock,
        ):
            request.user = reviewer
            ctx = membership_review(request)

        self.assertEqual(ctx["membership_requests_pending_count"], 0)
        self.assertEqual(ctx["membership_requests_on_hold_count"], 2)
        self.assertEqual(ctx["account_invitations_accepted_count"], 1)
        self.assertEqual(find_lightweight_mock.call_count, 1)
        self.assertEqual(set(find_lightweight_mock.call_args.args[0]), {"alice", "bob"})

    def test_context_processor_counts_exclude_orphaned_user_and_org_requests(self) -> None:
        from core.models import Organization

        MembershipRequest.objects.create(
            requested_username="live-user",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="orphan-user",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        org = Organization.objects.create(name="Orphan Org", representative="reviewer")
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )
        org.delete()

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )
        live_target_user = FreeIPAUser(
            "live-user",
            {
                "uid": ["live-user"],
                "mail": ["live-user@example.com"],
                "memberof_group": [],
            },
        )

        rf = RequestFactory()
        request = rf.get("/")

        with (
            patch("core.context_processors.FreeIPAUser.all", side_effect=AssertionError("membership_review should use the shared lightweight username helper")),
            patch(
                "core.context_processors.FreeIPAUser.find_lightweight_by_usernames",
                create=True,
                return_value={live_target_user.username: live_target_user},
            ) as find_lightweight_mock,
        ):
            request.user = reviewer
            ctx = membership_review(request)

        self.assertEqual(ctx["membership_requests_pending_count"], 1)
        self.assertEqual(ctx["membership_requests_on_hold_count"], 0)
        self.assertEqual(ctx["account_invitations_accepted_count"], 0)
        self.assertEqual(find_lightweight_mock.call_count, 1)
        self.assertEqual(set(find_lightweight_mock.call_args.args[0]), {"live-user", "orphan-user"})

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

    def test_visible_committee_membership_requests_accepts_precomputed_live_user_map(self) -> None:
        from core.models import Organization

        live_user = FreeIPAUser(
            "live-user",
            {
                "uid": ["live-user"],
                "mail": ["live-user@example.com"],
                "memberof_group": [],
            },
        )
        org = Organization.objects.create(name="Visible Org", representative="reviewer")
        live_user_request = MembershipRequest.objects.create(
            requested_username="live-user",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        orphan_user_request = MembershipRequest.objects.create(
            requested_username="ghost-user",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        org_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        org.delete()
        org_request.refresh_from_db()

        with patch("core.freeipa.user.FreeIPAUser.all", side_effect=AssertionError("should use the provided live-user map")):
            visible_requests = visible_committee_membership_requests(
                [live_user_request, orphan_user_request, org_request],
                live_users_by_username={live_user.username: live_user},
            )

        self.assertEqual([request.pk for request in visible_requests], [live_user_request.pk])
