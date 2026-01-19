from __future__ import annotations

from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)


class MembershipRequestLifecycleAndAuditLinkTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_multiple_requests_allowed_but_only_one_pending(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
        )
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        with self.assertRaises(IntegrityError):
            MembershipRequest.objects.create(
                requested_username="alice",
                membership_type_id="individual",
                status=MembershipRequest.Status.pending,
            )

    def test_approve_marks_request_approved_and_links_audit_log(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Contributions": "Hello committee"}],
        )

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

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True):
                resp = self.client.post(reverse("membership-request-approve", args=[req.pk]), follow=False)

        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.approved)
        self.assertIsNotNone(req.decided_at)
        self.assertEqual(req.decided_by_username, "reviewer")

        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
                membership_request=req,
            ).exists()
        )
