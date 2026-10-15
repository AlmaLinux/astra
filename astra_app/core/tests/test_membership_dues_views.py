from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipLog, MembershipRequest, Organization
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)
from core.tests.test_membership_dues import MembershipDuesTestMixin


class MembershipDuesViewTests(MembershipDuesTestMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        for username, permissions in (
            ("committee", (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP)),
            ("reviewer", (ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP)),
        ):
            for permission in permissions:
                FreeIPAPermissionGrant.objects.create(
                    permission=permission,
                    principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                    principal_name=username,
                )
        users = {
            username: FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})
            for username in ("committee", "reviewer", "rep1", "rep2")
        }
        self._users = users
        patcher = patch("core.freeipa.user.FreeIPAUser.get", side_effect=lambda username, **_kwargs: users.get(username))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _login(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _set_dues(self, **data):
        return self.client.post(
            reverse("organization-sponsorship-set-dues", args=[self.organization.pk, "gold"]),
            data=data,
        )

    def _messages(self, response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_committee_sets_dues_from_the_manage_membership_dialog(self) -> None:
        self._active_gold()
        self._login("committee")

        response = self._set_dues(dues_usd="0", dues_reason="Hosts our CI runners")

        self.assertRedirects(response, reverse("organization-detail", args=[self.organization.pk]), fetch_redirect_response=False)
        self.assertEqual(Membership.objects.get(target_organization=self.organization).dues_usd, 0)
        self.assertEqual(MembershipLog.objects.filter(action=MembershipLog.Action.dues_changed, actor_username="committee").count(), 1)
        self.assertIn("Sponsorship dues updated.", self._messages(response))

    def test_an_empty_amount_restores_the_standard_price(self) -> None:
        self._active_gold(dues_usd=5000)
        self._login("committee")

        response = self._set_dues(dues_usd="", dues_reason="In-kind agreement ended")

        self.assertIn("Sponsorship dues updated.", self._messages(response))
        self.assertIsNone(Membership.objects.get(target_organization=self.organization).dues_usd)
        [note] = self._dues_notes()
        self.assertEqual((note.action["old_dues_usd"], note.action["new_dues_usd"]), (5000, 20000))

    def test_dues_changes_need_a_reason_and_a_valid_amount(self) -> None:
        self._active_gold()
        self._login("committee")

        for data in ({"dues_usd": "0", "dues_reason": ""}, {"dues_usd": "25000", "dues_reason": "x"}, {"dues_usd": "abc", "dues_reason": "x"}):
            with self.subTest(data=data):
                response = self._set_dues(**data)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(self._messages(response))
                self.assertIsNone(Membership.objects.get(target_organization=self.organization).dues_usd)

    def test_only_members_who_can_edit_expiry_can_change_dues(self) -> None:
        self._active_gold()

        for username in ("reviewer", "rep1"):
            with self.subTest(username=username):
                self._login(username)
                self._set_dues(dues_usd="0", dues_reason="Trying my luck")
                self.assertIsNone(Membership.objects.get(target_organization=self.organization).dues_usd)

    def test_approve_api_accepts_committee_dues_with_a_reason(self) -> None:
        self._login("committee")
        missing_reason = self._request(self.gold)

        response = self.client.post(reverse("api-membership-request-approve", args=[missing_reason.pk]), data={"dues_usd": "0"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.json()["error"])

        response = self.client.post(
            reverse("api-membership-request-approve", args=[missing_reason.pk]),
            data={"dues_usd": "5000", "dues_reason": "Partial in-kind: build hardware"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Membership.objects.get(target_organization=self.organization).dues_usd, 5000)

    def test_approve_api_takes_a_full_waiver_from_a_json_body(self) -> None:
        self._login("committee")
        membership_request = self._request(self.gold)

        response = self.client.post(
            reverse("api-membership-request-approve", args=[membership_request.pk]),
            data={"dues_usd": 0, "dues_reason": "Mirror bandwidth in kind"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Membership.objects.get(target_organization=self.organization).dues_usd, 0)

    def test_reviewers_who_cannot_edit_expiry_cannot_set_dues_at_approval(self) -> None:
        self._login("reviewer")
        membership_request = self._request(self.gold)

        response = self.client.post(
            reverse("api-membership-request-approve", args=[membership_request.pk]),
            data={"dues_usd": "0", "dues_reason": "Trying my luck"},
        )

        self.assertEqual(response.status_code, 400)
        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)

    def test_bulk_approval_leaves_discounted_renewals_for_individual_review(self) -> None:
        self._active_gold(dues_usd=0)
        discounted_renewal = self._request(self.gold)
        other_org = Organization.objects.create(name="Other Org", representative="rep2")
        regular = MembershipRequest.objects.create(requested_organization=other_org, membership_type=self.ruby)
        self._login("committee")

        response = self.client.post(
            reverse("api-membership-requests-bulk"),
            data={"bulk_action": "accept", "bulk_scope": "pending", "selected": [discounted_renewal.pk, regular.pk]},
        )

        payload = response.json()
        self.assertEqual(payload["approved"], 1)
        self.assertEqual(payload["skipped_discounted"], [discounted_renewal.pk])
        discounted_renewal.refresh_from_db()
        self.assertEqual(discounted_renewal.status, MembershipRequest.Status.pending)

    def test_organization_page_shows_effective_dues_to_committee_and_representative(self) -> None:
        self._active_gold(dues_usd=5000)

        for username in ("committee", "rep1"):
            with self.subTest(username=username):
                self._login(username)
                response = self.client.get(reverse("api-organization-detail-page", args=[self.organization.pk]))
                [entry] = response.json()["organization"]["memberships"]
                self.assertEqual(entry["duesUsd"], 5000)
                self.assertEqual(entry["membershipType"]["annualDuesUsd"], 20000)

    def test_request_queue_and_detail_carry_the_dues_to_review(self) -> None:
        self._active_gold(dues_usd=5000)
        renewal = self._request(self.gold)
        self._login("committee")

        with patch("core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames", return_value={}):
            queue = self.client.get(
                reverse("api-membership-requests-pending"),
                data={
                    "draw": "1",
                    "start": "0",
                    "length": "25",
                    "order[0][column]": "0",
                    "order[0][dir]": "asc",
                    "order[0][name]": "requested_at",
                    "columns[0][data]": "request_id",
                    "columns[0][name]": "requested_at",
                    "columns[0][searchable]": "true",
                    "columns[0][orderable]": "true",
                    "queue_filter": "all",
                },
            ).json()
        [row] = [row for row in queue["data"] if row["request_id"] == renewal.pk]
        self.assertEqual(row["dues"], {"default_usd": 20000, "current_usd": 5000})

        detail = self.client.get(reverse("api-membership-request-detail", args=[renewal.pk])).json()
        self.assertEqual(detail["committee"]["dues"], {"default_usd": 20000, "current_usd": 5000})
        self.assertTrue(detail["committee"]["actions"]["canSetDues"])

        self._login("reviewer")
        detail = self.client.get(reverse("api-membership-request-detail", args=[renewal.pk])).json()
        self.assertFalse(detail["committee"]["actions"]["canSetDues"])

    def test_representatives_see_their_discount_but_cannot_change_it(self) -> None:
        self._active_gold(dues_usd=5000)
        renewal = self._request(self.gold)
        self._login("rep1")

        [entry] = self.client.get(reverse("api-organization-detail-page", args=[self.organization.pk])).json()["organization"]["memberships"]
        self.assertEqual(entry["duesUsd"], 5000)
        self.assertFalse(entry["canManage"])

        self._set_dues(dues_usd="0", dues_reason="We deserve it")
        approve = self.client.post(
            reverse("api-membership-request-approve", args=[renewal.pk]),
            data={"dues_usd": "0", "dues_reason": "We deserve it"},
        )

        self.assertEqual(approve.status_code, 403)
        renewal.refresh_from_db()
        self.assertEqual(renewal.status, MembershipRequest.Status.pending)
        self.assertEqual(Membership.objects.get(target_organization=self.organization).dues_usd, 5000)
        self.assertFalse(MembershipLog.objects.filter(action=MembershipLog.Action.dues_changed).exists())
        self.assertEqual(self._dues_notes(), [])

    def test_representatives_cannot_ask_for_a_discount_in_their_request(self) -> None:
        self._login("rep1")
        with (
            patch("core.views_membership.user.block_action_without_coc", return_value=None),
            patch("core.views_membership.user.block_action_without_country_code", return_value=None),
        ):
            self.client.post(
                reverse("organization-membership-request", args=[self.organization.pk]),
                data={
                    "membership_type": "gold",
                    "q_sponsorship_details": "Please waive our dues.",
                    "dues_usd": "0",
                    "dues_reason": "We deserve it",
                },
            )
        membership_request = MembershipRequest.objects.get(requested_organization=self.organization)

        self._login("committee")
        response = self.client.post(reverse("api-membership-request-approve", args=[membership_request.pk]))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(Membership.objects.get(target_organization=self.organization).dues_usd)
        self.assertEqual(self._dues_notes(), [])
