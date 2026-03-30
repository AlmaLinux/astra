
import datetime
import re
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

    def test_requests_list_shows_pending_and_on_hold_sections(self) -> None:
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

        pending = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        on_hold = MembershipRequest.objects.create(
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
        alice = self._make_freeipa_user("alice", email="alice@example.com")
        bob = self._make_freeipa_user("bob", email="bob@example.com")

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[reviewer, alice, bob]),
        ):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pending:")
        self.assertContains(resp, "Waiting for requester response")
        self.assertContains(resp, f"Request #{pending.pk}")
        self.assertContains(resp, f"Request #{on_hold.pk}")

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
        alice = self._make_freeipa_user("alice", email="alice@example.com")

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[reviewer, alice]),
        ):
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

    def test_on_hold_section_has_bulk_and_row_actions_and_waiting_inline(self) -> None:
        from django.utils import formats

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

        pending = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        on_hold_at = timezone.now() - datetime.timedelta(days=3)
        on_hold = MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=on_hold_at,
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        alice = self._make_freeipa_user("alice", email="alice@example.com")
        bob = self._make_freeipa_user("bob", email="bob@example.com")

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[reviewer, alice, bob]),
        ):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)

        # Pending keeps its own bulk UI.
        self.assertContains(resp, 'id="bulk-action-form"')
        self.assertContains(resp, f"Request #{pending.pk}")

        # On-hold section should have its own bulk UI + selection.
        self.assertContains(resp, 'id="bulk-action-form-on-hold"')
        self.assertContains(resp, 'name="bulk_scope" value="on_hold"')
        self.assertContains(resp, 'id="select-all-requests-on-hold"')

        # On-hold table should include reject/ignore buttons, but not approve (on_hold status).
        reject_url = reverse('membership-request-reject', args=[on_hold.pk])
        ignore_url = reverse('membership-request-ignore', args=[on_hold.pk])
        approve_url = reverse('membership-request-approve', args=[on_hold.pk])
        self.assertContains(resp, f'data-action-url="{reject_url}"')
        self.assertContains(resp, f'data-action-url="{ignore_url}"')
        self.assertNotContains(resp, f'data-action-url="{approve_url}"')

        # Waiting time should be rendered inline under "On hold since" (no separate "Waiting" column).
        self.assertNotContains(resp, ">Waiting</th>")
        # Use the same formatting Django templates use for "DATETIME_FORMAT".
        self.assertContains(resp, formats.date_format(on_hold_at, "DATETIME_FORMAT"))
        self.assertContains(resp, " ago")

    def test_requests_list_hides_requested_by_when_same_as_target_user(self) -> None:
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

        req_same = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipLog.objects.create(
            actor_username="alice",
            target_username="alice",
            membership_type=mt,
            membership_request=req_same,
            action=MembershipLog.Action.requested,
        )

        req_other = MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipLog.objects.create(
            actor_username="charlie",
            target_username="bob",
            membership_type=mt,
            membership_request=req_other,
            action=MembershipLog.Action.requested,
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        alice = self._make_freeipa_user("alice", email="alice@example.com")
        bob = self._make_freeipa_user("bob", email="bob@example.com")
        charlie = self._make_freeipa_user("charlie", email="charlie@example.com")

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[reviewer, alice, bob, charlie]),
        ):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        alice_profile = reverse("user-profile", args=["alice"])
        bob_profile = reverse("user-profile", args=["bob"])
        charlie_profile = reverse("user-profile", args=["charlie"])

        # Target links exist for both requests.
        self.assertIn(alice_profile, content)
        self.assertIn(bob_profile, content)

        # When the requester is the same as the target, the extra "Requested by" line is omitted.
        self.assertNotRegex(content, rf"Requested by:\s*<a href=\"{re.escape(alice_profile)}\"")

        # When they differ, it is shown.
        self.assertRegex(content, rf"Requested by:\s*<a href=\"{re.escape(charlie_profile)}\"")

    def test_requests_list_paginates_pending_rows_and_preserves_query_params(self) -> None:
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

        for idx in range(55):
            MembershipRequest.objects.create(
                requested_username=f"user{idx}",
                membership_type_id="individual",
                status=MembershipRequest.Status.pending,
            )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        all_users = [
            reviewer,
            *[
                self._make_freeipa_user(f"user{idx}", email=f"user{idx}@example.com")
                for idx in range(55)
            ],
        ]

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=all_users),
        ):
            page_1 = self.client.get(f"{reverse('membership-requests')}?scope=all")
            page_2 = self.client.get(f"{reverse('membership-requests')}?scope=all&pending_page=2")

        self.assertEqual(page_1.status_code, 200)
        self.assertEqual(page_2.status_code, 200)

        pending_page_1 = page_1.context["pending_page_obj"]
        pending_page_2 = page_2.context["pending_page_obj"]
        self.assertEqual(pending_page_1.number, 1)
        self.assertEqual(pending_page_1.paginator.per_page, 50)
        self.assertEqual(len(pending_page_1.object_list), 50)
        self.assertEqual(pending_page_2.number, 2)
        self.assertEqual(len(pending_page_2.object_list), 5)

        self.assertContains(page_1, "?scope=all&amp;pending_page=2")
        self.assertContains(page_2, "?scope=all&amp;pending_page=1")
