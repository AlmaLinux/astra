import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipNotesApiEndpointTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

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

    def _login_as(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _reviewer(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def _viewer(self) -> FreeIPAUser:
        return FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

    def test_request_notes_add_api_url_is_distinct_from_get_url(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        get_url = reverse("api-membership-request-notes", args=[req.pk])
        add_url = reverse("api-membership-request-notes-add", args=[req.pk])

        self.assertEqual(get_url, f"/api/v1/membership/notes/{req.pk}")
        self.assertEqual(add_url, f"/api/v1/membership/requests/{req.pk}/notes/add")

    def test_request_notes_add_api_persists_note_and_returns_json(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self._login_as("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello from API",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload, {"ok": True, "message": "Note added."})
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                content="Hello from API",
            ).exists()
        )

    def test_request_notes_add_api_returns_json_only_without_legacy_html_payload(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self._login_as("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={"note_action": "message", "message": "json-only"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Note added."})
        self.assertNotIn("html", response.json())

    def test_request_notes_add_api_allows_change_permission_without_add_permission(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self._login_as("change_only")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="change_only",
        )

        manager = FreeIPAUser(
            "change_only",
            {
                "uid": ["change_only"],
                "mail": ["change_only@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={"note_action": "message", "message": "change write"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Note added."})
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="change_only",
                content="change write",
            ).exists()
        )

    def test_aggregate_add_api_persists_to_latest_pending_then_returns_json(self) -> None:
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )
        old_request = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        latest_pending = MembershipRequest.objects.create(requested_username="alice", membership_type_id="mirror")
        self._login_as("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "message",
                    "message": "aggregate note",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Note added."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=old_request,
                username="reviewer",
                content="aggregate note",
            ).exists()
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=latest_pending,
                username="reviewer",
                content="aggregate note",
            ).exists()
        )

    def test_aggregate_add_api_rejects_invalid_note_action(self) -> None:
        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self._login_as("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "vote_approve",
                    "message": "forged",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid note action."})

    def test_aggregate_add_api_allows_delete_permission_without_add_permission(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self._login_as("delete_only")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_DELETE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="delete_only",
        )

        manager = FreeIPAUser(
            "delete_only",
            {
                "uid": ["delete_only"],
                "mail": ["delete_only@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "message",
                    "message": "delete write",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Note added."})
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="delete_only",
                content="delete write",
            ).exists()
        )

    def test_aggregate_add_api_denies_without_manage_permission(self) -> None:
        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        self._login_as("viewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._viewer()):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "message",
                    "message": "denied",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Permission denied."})

    def test_request_notes_summary_api_returns_vote_counts(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", action={"type": "vote", "value": "approve"})
        Note.objects.create(membership_request=req, username="someone", action={"type": "vote", "value": "disapprove"})
        Note.objects.create(membership_request=req, username="reviewer", content="message", action={})
        self._login_as("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.get(
                reverse("api-membership-request-notes-summary", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "note_count": 3,
                "approvals": 1,
                "disapprovals": 1,
                "current_user_vote": "approve",
            },
        )

    def test_request_notes_detail_api_returns_rendered_entries(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="**Bold**", action={})
        Note.objects.create(membership_request=req, username="reviewer", content="<script>bad()</script>", action={})
        self._login_as("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rendered = json.dumps(payload)
        self.assertIn("<strong>Bold</strong>", rendered)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", rendered)
        self.assertNotIn("<script>bad()</script>", rendered)

    def test_request_notes_detail_api_groups_consecutive_actions_from_same_author(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        first = Note.objects.create(
            membership_request=req,
            username="reviewer",
            content=None,
            action={"type": "request_on_hold"},
        )
        second = Note.objects.create(
            membership_request=req,
            username="reviewer",
            content=None,
            action={"type": "contacted"},
        )
        base_timestamp = first.timestamp
        Note.objects.filter(pk=second.pk).update(timestamp=base_timestamp)

        self._login_as("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        groups = payload["groups"]
        self.assertGreaterEqual(len(groups), 1)

        reviewer_group = groups[0]
        self.assertEqual(reviewer_group["username"], "reviewer")
        self.assertEqual(len(reviewer_group["entries"]), 2)
        self.assertEqual(reviewer_group["entries"][0]["kind"], "action")
        self.assertEqual(reviewer_group["entries"][1]["kind"], "action")

    def test_request_notes_detail_api_marks_custos_entries_and_avatar_kind(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username=CUSTOS,
            content="system note",
            action={},
        )

        self._login_as("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        custos_group = payload["groups"][0]
        self.assertEqual(custos_group["avatar_kind"], "custos")
        self.assertEqual(custos_group["is_custos"], True)
        self.assertIn("almalinux-logo.svg", custos_group["avatar_url"])

    def test_request_notes_detail_api_includes_request_resubmitted_diff_rows(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Contributions": "Cycle 2"}, {"Additional info": "Same"}],
        )
        Note.objects.create(
            membership_request=req,
            username="alice",
            content=None,
            action={
                "type": "request_resubmitted",
                "old_responses": [{"Contributions": "Cycle 1"}, {"Additional info": "Same"}],
            },
        )

        self._login_as("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entry = payload["groups"][0]["entries"][0]
        self.assertEqual(entry["kind"], "action")
        self.assertGreaterEqual(len(entry["request_resubmitted_diff_rows"]), 1)
        self.assertEqual(entry["request_resubmitted_diff_rows"][0]["question"], "Contributions")
        self.assertIn("Cycle 1", entry["request_resubmitted_diff_rows"][0]["old_value"])
        self.assertIn("Cycle 2", entry["request_resubmitted_diff_rows"][0]["new_value"])

    def test_request_notes_summary_api_uses_latest_vote_for_current_user(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", action={"type": "vote", "value": "approve"})
        Note.objects.create(membership_request=req, username="reviewer", action={"type": "vote", "value": "disapprove"})
        Note.objects.create(membership_request=req, username="other", action={"type": "vote", "value": "approve"})

        self._login_as("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.get(
                reverse("api-membership-request-notes-summary", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "note_count": 3,
                "approvals": 1,
                "disapprovals": 1,
                "current_user_vote": "disapprove",
            },
        )

    def test_aggregate_add_api_rejects_invalid_org_target(self) -> None:
        self._login_as("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "org",
                    "target": "not-an-int",
                    "note_action": "message",
                    "message": "invalid org",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid target."})
