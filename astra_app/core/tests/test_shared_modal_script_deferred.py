"""Verify modal and action wiring contracts for membership-related pages."""

from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipRequestDetailActionContractsTests(TestCase):
    """Membership request detail actions are now Vue-owned on the detail page."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
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
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )

    def _login_as_committee(self, username: str = "reviewer") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_request_detail_uses_vue_actions_root(self) -> None:
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        self._login_as_committee()

        membership_request = MembershipRequest.objects.first()
        assert membership_request is not None

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()
        self.assertIn('data-membership-request-detail-root=""', content)
        self.assertIn('/api/v1/membership/requests/', content)
        self.assertNotIn('id="shared-approve-modal"', content)
        self.assertNotIn('src="/static/core/js/membership_request_shared_modals.js"', content)


class GroupDetailModalScriptDeferredTests(TestCase):
    """Group detail modal JS must defer jQuery usage until DOMContentLoaded."""

    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_modal_script_uses_domcontentloaded(self) -> None:
        from core.freeipa.group import FreeIPAGroup

        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "testgrp",
            {
                "cn": ["testgrp"],
                "description": ["A group"],
                "member_user": ["admin", "alice"],
                "member_group": [],
                "membermanager_user": ["admin"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "displayname": ["Administrator"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
        ):
            resp = self.client.get("/group/testgrp/")

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        self.assertIn('data-group-detail-root', content)
        self.assertIn('src="http://localhost:5173/src/entrypoints/groupDetail.ts"', content)
