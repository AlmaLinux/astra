
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_USER_DIRECTORY
from core.tests.utils_test_data import ensure_core_categories


class MembershipNotificationsDropdownSplitTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_USER_DIRECTORY,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_notifications_dropdown_splits_pending_and_awaiting_action(self) -> None:
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
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        self._login_as_freeipa_user("reviewer")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]),
        ):
            resp = self.client.get(reverse("users"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pending requests")
        self.assertContains(resp, "On hold requests")
        self.assertNotContains(resp, ">1/1<")
