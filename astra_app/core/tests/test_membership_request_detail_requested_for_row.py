
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipLog, MembershipRequest, MembershipType
from core.permissions import ASTRA_VIEW_MEMBERSHIP


class MembershipRequestDetailRequestedForRowTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_requested_for_row_hidden_when_same_as_requested_by(self) -> None:
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
        mt = MembershipType.objects.get(code="individual")

        req = MembershipRequest.objects.create(requested_username="alice", membership_type=mt)
        MembershipLog.objects.create(
            actor_username="alice",
            target_username="alice",
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type=mt,
            membership_request=req,
            requested_group_cn=mt.group_cn,
            action=MembershipLog.Action.requested,
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "givenname": ["Alice"],
                "sn": ["User"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"reviewer": reviewer, "alice": alice}.get(username)

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Requested by")
        self.assertContains(resp, "Alice User")
        self.assertContains(resp, "(alice)")
        self.assertNotContains(resp, "Requested for")

    def test_requested_for_row_shown_and_formatted_when_different(self) -> None:
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
        mt = MembershipType.objects.get(code="individual")

        req = MembershipRequest.objects.create(requested_username="bob", membership_type=mt)
        MembershipLog.objects.create(
            actor_username="alice",
            target_username="bob",
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type=mt,
            membership_request=req,
            requested_group_cn=mt.group_cn,
            action=MembershipLog.Action.requested,
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "givenname": ["Alice"],
                "sn": ["User"],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
                "givenname": ["Bob"],
                "sn": ["User"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"reviewer": reviewer, "alice": alice, "bob": bob}.get(username)

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Requested by")
        self.assertContains(resp, "Alice User")
        self.assertContains(resp, "(alice)")
        self.assertContains(resp, "Requested for")
        self.assertContains(resp, "Bob User")
        self.assertContains(resp, "(bob)")
