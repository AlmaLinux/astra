
import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipLog, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipRequestsOnHoldSplitTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _make_freeipa_user(
        self,
        username: str,
        *,
        email: str | None = None,
        groups: list[str] | None = None,
    ) -> FreeIPAUser:
        user_data: dict[str, list[str]] = {
            "uid": [username],
            "memberof_group": list(groups or []),
        }
        if email is not None:
            user_data["mail"] = [email]
        return FreeIPAUser(username, user_data)

    def _datatables_query(self, *, order_name: str, length: int) -> dict[str, str]:
        return {
            "draw": "1",
            "start": "0",
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "order[0][name]": order_name,
            "columns[0][data]": "request_id",
            "columns[0][name]": order_name,
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

    def test_requests_page_renders_pending_and_on_hold_shells(self) -> None:
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
            on_hold_at=timezone.now() - datetime.timedelta(days=3),
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pending:")
        self.assertContains(resp, "Waiting for requester response")
        self.assertContains(resp, 'data-membership-requests-root')
        self.assertContains(resp, 'id="membership-requests-pending-table"')
        self.assertContains(resp, 'id="membership-requests-on-hold-table"')
        self.assertContains(resp, 'id="bulk-action-form"')
        self.assertContains(resp, 'id="bulk-action-form-on-hold"')
        self.assertContains(resp, '>On hold since</th>')
        self.assertNotContains(resp, ">Waiting</th>")
        self.assertNotContains(resp, "Request #1")

    def test_reject_modal_includes_reason_presets(self) -> None:
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

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            "This decision is due to legal requirements that currently prevent the AlmaLinux OS Foundation from "
            "approving applications from certain countries.",
        )
        self.assertContains(
            resp,
            "We were unable to complete the approval process because we did not receive the additional information "
            "requested during our review.",
        )

    def test_pending_endpoint_hides_requested_by_when_same_as_target_user(self) -> None:
        membership_type = MembershipType.objects.create(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
            category_id="individual",
            sort_order=0,
            enabled=True,
        )
        same_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )
        MembershipLog.objects.create(
            actor_username="alice",
            target_username="alice",
            membership_type=membership_type,
            membership_request=same_request,
            action=MembershipLog.Action.requested,
        )
        other_request = MembershipRequest.objects.create(
            requested_username="bob",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )
        MembershipLog.objects.create(
            actor_username="charlie",
            target_username="bob",
            membership_type=membership_type,
            membership_request=other_request,
            action=MembershipLog.Action.requested,
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        users = [
            reviewer,
            self._make_freeipa_user("alice", email="alice@example.com"),
            self._make_freeipa_user("bob", email="bob@example.com"),
            self._make_freeipa_user("charlie", email="charlie@example.com"),
        ]

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in users if user.username},
            ),
        ):
            resp = self.client.get(
                reverse("api-membership-requests-pending"),
                data={
                    **self._datatables_query(order_name="requested_at", length=50),
                    "queue_filter": "all",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        rows = {row["request_id"]: row for row in resp.json()["data"]}
        self.assertFalse(rows[same_request.pk]["requested_by"]["show"])
        self.assertTrue(rows[other_request.pk]["requested_by"]["show"])
