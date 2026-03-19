from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, MembershipTypeCategory
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL, ASTRA_VIEW_MEMBERSHIP


class MembershipRequestCanonicalDetailUrlTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
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

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP, ASTRA_ADD_SEND_MAIL):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=committee_cn,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_reverse_membership_request_detail_is_canonical_path(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self.assertEqual(
            reverse("membership-request-detail", args=[req.pk]),
            f"/membership/request/{req.pk}/",
        )

    def test_committee_can_open_canonical_detail_page(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _freeipa_get_side_effect(username: str) -> FreeIPAUser | None:
            return {
                "reviewer": reviewer,
                "alice": alice,
            }.get(username)

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_freeipa_get_side_effect):
            resp = self.client.get(f"/membership/request/{req.pk}/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("membership-requests"))
        self.assertContains(resp, reverse("membership-request-approve", args=[req.pk]))

    def test_legacy_detail_redirects_to_canonical_for_committee(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        def _freeipa_get_side_effect(username: str) -> FreeIPAUser | None:
            return {
                "reviewer": reviewer,
            }.get(username)

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_freeipa_get_side_effect):
            resp = self.client.get(f"/membership/requests/{req.pk}/", follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], f"/membership/request/{req.pk}/")

    def test_legacy_detail_returns_404_for_unauthorized_viewer(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _freeipa_get_side_effect(username: str) -> FreeIPAUser | None:
            return {
                "bob": bob,
            }.get(username)

        self._login_as_freeipa_user("bob")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_freeipa_get_side_effect):
            resp = self.client.get(f"/membership/requests/{req.pk}/", follow=False)

        self.assertEqual(resp.status_code, 404)
