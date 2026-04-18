
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)
from core.tests.utils_test_data import ensure_core_categories


class MembershipNotesAjaxTests(TestCase):
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

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _reviewer_user(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def _aggregate_target_token(self, *, username: str, email: str = "") -> str:
        from core.tokens import make_membership_notes_aggregate_target_token

        return make_membership_notes_aggregate_target_token(
            {
                "target_type": "user",
                "target": username,
                "email": email,
            }
        )

    def _aggregate_request(self, user: object):
        request = RequestFactory().get("/")
        request.user = user
        viewer_username = ""
        if hasattr(user, "username"):
            viewer_username = str(user.username or "").strip()
        request.session = {"_freeipa_username": viewer_username}
        return request

    def _group_html(self, html: str, username: str, *, next_username: str | None = None) -> str:
        start_marker = f'data-membership-notes-group-username="{username}"'
        start_index = html.find(start_marker)
        self.assertNotEqual(start_index, -1)
        if next_username is None:
            return html[start_index:]

        end_marker = f'data-membership-notes-group-username="{next_username}"'
        end_index = html.find(end_marker, start_index + len(start_marker))
        self.assertNotEqual(end_index, -1)
        return html[start_index:end_index]

    def _render_notes_html_via_ajax(self, membership_request_id: int) -> str:
        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[membership_request_id]),
                data={
                    "note_action": "message",
                    "message": "render",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))
        return str(payload.get("html", ""))

    def test_note_add_returns_json_and_updated_html_for_ajax(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))
        self.assertIn("html", payload)
        self.assertIn("Hello via ajax", payload["html"])
        self.assertIn("Membership Committee Notes", payload["html"])

        # Non-compact widgets render expanded by default.
        self.assertIsNone(
            re.search(
                rf'id="membership-notes-card-{req.pk}"[^>]*class="[^"]*\bcollapsed-card\b',
                payload["html"],
            )
        )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                content="Hello via ajax",
            ).exists()
        )

    def test_other_user_bubbles_get_deterministic_inline_bubble_style(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp1 = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Self note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp1.status_code, 200)
        payload1 = json.loads(resp1.content)
        self.assertTrue(payload1.get("ok"))
        self.assertNotIn(
            'class="direct-chat-text membership-notes-bubble" style="--bubble-bg:',
            payload1.get("html", ""),
        )

        Note.objects.create(
            membership_request=req,
            username="someone_else",
            content="Other note",
            action={},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp2 = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Another self note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp2.status_code, 200)
        payload2 = json.loads(resp2.content)
        self.assertTrue(payload2.get("ok"))
        html2 = payload2.get("html", "")
        self.assertIn('class="direct-chat-text membership-notes-bubble"', html2)
        self.assertIn("--bubble-bg:", html2)

    def test_custos_notes_render_with_distinct_style_and_avatar(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        # Pre-seed a system note so the rendered widget includes it.
        Note.objects.create(
            membership_request=req,
            username=CUSTOS,
            content="system note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        html = payload.get("html", "")
        self.assertTrue(payload.get("ok"))

        self.assertIn("Astra Custodia", html)
        self.assertIn("core/images/almalinux-logo.svg", html)
        self.assertIn("--bubble-bg: #e9ecef", html)

    def test_mirror_validation_notes_render_multiline_with_bold_result_values(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username=CUSTOS,
            content=(
                "Mirror validation summary\n"
                "Domain: reachable\n"
                "Mirror status: up-to-date\n"
                "AlmaLinux mirror network: registered\n"
                "GitHub pull request: valid; touches mirrors.d/mirror.example.org.yml"
            ),
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        html = payload.get("html", "")
        self.assertTrue(payload.get("ok"))
        self.assertIn("Mirror validation summary<br>", html)
        self.assertIn("Domain: <strong>reachable</strong>", html)
        self.assertIn("Mirror status: <strong>up-to-date</strong>", html)
        self.assertIn("AlmaLinux mirror network: <strong>registered</strong>", html)
        self.assertIn(
            "GitHub pull request: <strong>valid; touches mirrors.d/mirror.example.org.yml</strong>",
            html,
        )

    def test_regular_notes_render_safe_markdown_subset(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username="someone_else",
            content=(
                "**Bold** and *italic*\n\n"
                "- first item\n"
                "- second item\n\n"
                "<script>alert('x')</script>"
            ),
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        html = payload.get("html", "")
        self.assertTrue(payload.get("ok"))
        self.assertIn("<strong>Bold</strong>", html)
        self.assertIn("<em>italic</em>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li>first item</li>", html)
        self.assertIn("<li>second item</li>", html)
        self.assertIn("&lt;script&gt;alert", html)
        self.assertNotIn("<script>alert", html)

    def test_regular_note_html_remains_escaped(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username="someone_else",
            content="<em>not safe</em>",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        html = payload.get("html", "")
        self.assertTrue(payload.get("ok"))
        self.assertIn("&lt;em&gt;not safe&lt;/em&gt;", html)
        self.assertNotIn("<em>not safe</em>", html)

    def test_consecutive_actions_by_same_user_within_minute_are_grouped(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        now = timezone.now()
        base = now - timedelta(minutes=5)
        n1 = Note.objects.create(
            membership_request=req,
            username="alex",
            content=None,
            action={"type": "request_on_hold"},
        )
        n2 = Note.objects.create(
            membership_request=req,
            username="alex",
            content=None,
            action={"type": "contacted"},
        )
        Note.objects.filter(pk=n1.pk).update(timestamp=base)
        Note.objects.filter(pk=n2.pk).update(timestamp=base + timedelta(seconds=30))

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))
        html = payload.get("html", "")

        # The two consecutive alex actions should render as one grouped row
        # (one username header + two bubbles).
        marker = 'data-membership-notes-group-username="alex"'
        start = html.find(marker)
        self.assertNotEqual(start, -1, "Expected a grouped row marker for alex")
        end = html.find('data-membership-notes-group-username="', start + len(marker))
        group_html = html[start:] if end == -1 else html[start:end]

        self.assertEqual(group_html.count("direct-chat-infos"), 1)
        self.assertIn("Request on hold", group_html)
        self.assertIn("User contacted", group_html)
        bubble_class_hits = re.findall(r'\bmembership-notes-bubble\b', group_html)
        self.assertEqual(len(bubble_class_hits), 2)

    def test_view_only_user_cannot_submit_vote_actions(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "approve",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="viewer",
                action={"type": "vote", "value": "approve"},
            ).exists()
        )

    def test_view_only_user_cannot_submit_plain_message_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "plain note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="viewer",
                content="plain note",
            ).exists()
        )

    def test_view_only_user_cannot_submit_plain_aggregate_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "note_action": "message",
                    "message": "aggregate note",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="viewer",
                content="aggregate note",
            ).exists()
        )

    def test_manage_user_forged_aggregate_action_returns_deterministic_ajax_deny(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "note_action": "vote_approve",
                    "message": "forged aggregate action",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                content="forged aggregate action",
            ).exists()
        )

    def test_no_membership_permissions_user_cannot_submit_plain_detail_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("no_permissions")
        no_permissions_user = FreeIPAUser(
            "no_permissions",
            {
                "uid": ["no_permissions"],
                "mail": ["no-permissions@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=no_permissions_user):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "detail note denied",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="no_permissions",
                content="detail note denied",
            ).exists()
        )

    def test_no_membership_permissions_user_cannot_submit_plain_aggregate_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("no_permissions")
        no_permissions_user = FreeIPAUser(
            "no_permissions",
            {
                "uid": ["no_permissions"],
                "mail": ["no-permissions@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=no_permissions_user):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "note_action": "message",
                    "message": "aggregate note denied",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="no_permissions",
                content="aggregate note denied",
            ).exists()
        )

    def test_no_membership_permissions_detail_ajax_does_not_leak_request_existence(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        missing_pk = req.pk + 999

        self._login_as_freeipa_user("no_permissions")
        no_permissions_user = FreeIPAUser(
            "no_permissions",
            {
                "uid": ["no_permissions"],
                "mail": ["no-permissions@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=no_permissions_user):
            existing_resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "probe note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            missing_resp = self.client.post(
                reverse("membership-request-note-add", args=[missing_pk]),
                data={
                    "note_action": "message",
                    "message": "probe note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(existing_resp.status_code, 403)
        self.assertEqual(existing_resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertEqual(missing_resp.status_code, 403)
        self.assertEqual(missing_resp.json(), {"ok": False, "error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="no_permissions",
                content="probe note",
            ).exists()
        )

    def test_any_single_manage_permission_can_submit_plain_detail_note_without_view_permission(self) -> None:
        for index, permission in enumerate(
            (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP),
            start=1,
        ):
            username = f"manager{index}"
            request_username = f"alice-manage-{index}"
            req = MembershipRequest.objects.create(requested_username=request_username, membership_type_id="individual")

            FreeIPAPermissionGrant.objects.get_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=username,
            )

            self._login_as_freeipa_user(username)
            manager = FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "mail": [f"{username}@example.com"],
                    "memberof_group": [],
                },
            )

            content = f"detail note {permission}"
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
                resp = self.client.post(
                    reverse("membership-request-note-add", args=[req.pk]),
                    data={
                        "note_action": "message",
                        "message": content,
                        "next": reverse("membership-requests"),
                    },
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(resp.status_code, 200, msg=permission)
            self.assertTrue(resp.json().get("ok"), msg=permission)
            self.assertTrue(
                Note.objects.filter(
                    membership_request=req,
                    username=username,
                    content=content,
                ).exists(),
                msg=permission,
            )

    def test_any_single_manage_permission_can_submit_plain_aggregate_note_without_view_permission(self) -> None:
        for index, permission in enumerate(
            (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP),
            start=1,
        ):
            username = f"aggregate_manager{index}"
            target_username = f"aggregate-target-{index}"
            req = MembershipRequest.objects.create(requested_username=target_username, membership_type_id="individual")

            FreeIPAPermissionGrant.objects.get_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=username,
            )

            self._login_as_freeipa_user(username)
            manager = FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "mail": [f"{username}@example.com"],
                    "memberof_group": [],
                },
            )

            content = f"aggregate note {permission}"
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
                resp = self.client.post(
                    reverse("membership-notes-aggregate-note-add"),
                    data={
                        "aggregate_target_type": "user",
                        "aggregate_target": target_username,
                        "note_action": "message",
                        "message": content,
                        "compact": "1",
                        "next": reverse("membership-requests"),
                    },
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(resp.status_code, 200, msg=permission)
            self.assertTrue(resp.json().get("ok"), msg=permission)
            self.assertTrue(
                Note.objects.filter(
                    membership_request=req,
                    username=username,
                    content=content,
                ).exists(),
                msg=permission,
            )

    def test_aggregate_ajax_context_does_not_force_membership_can_view(self) -> None:
        req = MembershipRequest.objects.create(requested_username="aggregate-target", membership_type_id="individual")

        manager_username = "aggregate_context_manager"
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=manager_username,
        )

        self._login_as_freeipa_user(manager_username)
        manager = FreeIPAUser(
            manager_username,
            {
                "uid": [manager_username],
                "mail": [f"{manager_username}@example.com"],
                "memberof_group": [],
            },
        )

        captured_context: dict[str, object] = {}

        def _capture_context(context: dict[str, object], username: str, *, compact: bool, next_url: str) -> str:
            del username
            del compact
            del next_url
            captured_context.update(context)
            return "<div>captured aggregate widget</div>"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=manager),
            patch(
                "core.views_membership.committee.membership_review_permissions",
                return_value={
                    "membership_can_add": False,
                    "membership_can_change": True,
                    "membership_can_delete": False,
                    "membership_can_view": False,
                    "send_mail_can_add": False,
                },
            ),
            patch(
                "core.templatetags.core_membership_notes.membership_notes_aggregate_for_user",
                side_effect=_capture_context,
            ),
        ):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": req.requested_username,
                    "note_action": "message",
                    "message": "context probe note",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(captured_context.get("membership_can_view"), False)
        self.assertEqual(captured_context.get("membership_can_change"), True)
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username=manager_username,
                content="context probe note",
            ).exists()
        )

    def test_aggregate_malformed_org_target_returns_deterministic_400_without_persistence(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "org",
                    "aggregate_target": "not-an-int",
                    "note_action": "message",
                    "message": "bad org target",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"ok": False, "error": "Invalid target."})
        self.assertFalse(
            Note.objects.filter(
                username="reviewer",
                content="bad org target",
            ).exists()
        )

    def test_membership_notes_template_hides_compose_for_read_only_viewers(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="Visible note")

        request = RequestFactory().get("/membership-requests")
        request.session = {"_freeipa_username": "viewer"}

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            from core.templatetags.core_membership_notes import membership_notes

            html = str(
                membership_notes(
                    {
                        "request": request,
                        "membership_can_view": True,
                        "membership_can_add": False,
                        "membership_can_change": False,
                        "membership_can_delete": False,
                    },
                    req,
                    compact=False,
                    next_url="/membership-requests",
                )
            )

        self.assertIn("Membership Committee Notes", html)
        self.assertIn("Visible note", html)
        self.assertNotIn('data-membership-notes-form="', html)
        self.assertNotIn('placeholder="Type a note..."', html)

    def test_membership_notes_fail_fast_rejects_invalid_preloaded_notes(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        request = RequestFactory().get("/membership-requests")
        request.session = {"_freeipa_username": "viewer"}

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            from core.templatetags.core_membership_notes import membership_notes

            with self.assertRaisesRegex(RuntimeError, "requires a valid preloaded notes list"):
                membership_notes(
                    {
                        "request": request,
                        "membership_can_view": True,
                        "membership_can_add": False,
                        "membership_can_change": False,
                        "membership_can_delete": False,
                    },
                    req,
                    compact=True,
                    next_url="/membership-requests",
                    preloaded_notes=[{"bad": "value"}],
                    fail_on_query_fallback=True,
                )

    def test_manage_user_can_submit_vote_actions(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "approve",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "vote", "value": "approve"},
            ).exists()
        )

    def test_vote_badges_highlight_reviewers_latest_vote(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            first = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(first.status_code, 200)
        first_payload = json.loads(first.content)
        self.assertTrue(first_payload.get("ok"))
        first_html = first_payload.get("html", "")
        first_approvals = re.search(
            rf'<span[^>]*data-membership-notes-approvals="{req.pk}"[^>]*>',
            first_html,
            re.DOTALL,
        )
        self.assertIsNotNone(first_approvals)
        assert first_approvals is not None
        self.assertIn("badge-warning", first_approvals.group(0))

        first_disapprovals = re.search(
            rf'<span[^>]*data-membership-notes-disapprovals="{req.pk}"[^>]*>',
            first_html,
            re.DOTALL,
        )
        self.assertIsNotNone(first_disapprovals)
        assert first_disapprovals is not None
        self.assertIn("badge-danger", first_disapprovals.group(0))

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            second = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "vote_disapprove",
                    "message": "",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(second.status_code, 200)
        second_payload = json.loads(second.content)
        self.assertTrue(second_payload.get("ok"))
        second_html = second_payload.get("html", "")
        second_approvals = re.search(
            rf'<span[^>]*data-membership-notes-approvals="{req.pk}"[^>]*>',
            second_html,
            re.DOTALL,
        )
        self.assertIsNotNone(second_approvals)
        assert second_approvals is not None
        self.assertIn("badge-success", second_approvals.group(0))

        second_disapprovals = re.search(
            rf'<span[^>]*data-membership-notes-disapprovals="{req.pk}"[^>]*>',
            second_html,
            re.DOTALL,
        )
        self.assertIsNotNone(second_disapprovals)
        assert second_disapprovals is not None
        self.assertIn("badge-warning", second_disapprovals.group(0))

    def test_request_resubmitted_diff_is_stable_across_multi_cycle_history(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[
                {"Contributions": "Cycle 3"},
                {"Additional info": "Same"},
            ],
        )
        note_1 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 1"},
                    {"Additional info": "Same"},
                ],
            },
        )
        note_2 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 2"},
                    {"Additional info": "Same"},
                ],
            },
        )

        html = self._render_notes_html_via_ajax(req.pk)

        marker_1 = f'data-request-resubmitted-note-id="{note_1.pk}"'
        marker_2 = f'data-request-resubmitted-note-id="{note_2.pk}"'
        start_1 = html.find(marker_1)
        start_2 = html.find(marker_2)
        self.assertNotEqual(start_1, -1)
        self.assertNotEqual(start_2, -1)
        note_1_html = html[start_1:start_2]
        note_2_html = html[start_2:]

        self.assertIn("Cycle 1", note_1_html)
        self.assertIn("Cycle 2", note_1_html)
        self.assertNotIn("Cycle 3", note_1_html)

        self.assertIn("Cycle 2", note_2_html)
        self.assertIn("Cycle 3", note_2_html)

    def test_request_resubmitted_diff_uses_pk_tiebreak_for_equal_timestamps(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[
                {"Contributions": "Cycle 3"},
                {"Additional info": "Same"},
            ],
        )
        note_1 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 1"},
                    {"Additional info": "Same"},
                ],
            },
        )
        note_2 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 2"},
                    {"Additional info": "Same"},
                ],
            },
        )
        tie_timestamp = timezone.now() - timedelta(minutes=5)
        Note.objects.filter(pk=note_1.pk).update(timestamp=tie_timestamp)
        Note.objects.filter(pk=note_2.pk).update(timestamp=tie_timestamp)

        html = self._render_notes_html_via_ajax(req.pk)

        marker_1 = f'data-request-resubmitted-note-id="{note_1.pk}"'
        marker_2 = f'data-request-resubmitted-note-id="{note_2.pk}"'
        start_1 = html.find(marker_1)
        start_2 = html.find(marker_2)
        self.assertNotEqual(start_1, -1)
        self.assertNotEqual(start_2, -1)
        self.assertLess(start_1, start_2)

        note_1_html = html[start_1:start_2]
        note_2_html = html[start_2:]

        self.assertIn("Cycle 1", note_1_html)
        self.assertIn("Cycle 2", note_1_html)
        self.assertNotIn("Cycle 3", note_1_html)

        self.assertIn("Cycle 2", note_2_html)
        self.assertIn("Cycle 3", note_2_html)

    def test_request_resubmitted_diff_renders_changed_questions_as_collapsed_details_only(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[
                {"Changed question": "Updated value"},
                {"Unchanged question": "Same value"},
            ],
        )
        note = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Changed question": "Old value"},
                    {"Unchanged question": "Same value"},
                ],
            },
        )

        html = self._render_notes_html_via_ajax(req.pk)
        marker = f'data-request-resubmitted-note-id="{note.pk}"'
        start = html.find(marker)
        self.assertNotEqual(start, -1)
        note_html = html[start:]

        self.assertIn('data-request-resubmitted-question="Changed question"', note_html)
        self.assertIn("<details", note_html)
        self.assertNotIn("<details open", note_html)
        self.assertNotIn('data-request-resubmitted-question="Unchanged question"', note_html)

    @override_settings(MEMBERSHIP_NOTES_RESUBMITTED_DIFFS_ENABLED=False)
    def test_request_resubmitted_diff_can_be_disabled_without_hiding_notes(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Contributions": "Updated"}],
        )
        Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [{"Contributions": "Original"}],
            },
        )

        detail_html = self._render_notes_html_via_ajax(req.pk)
        self.assertIn("Request resubmitted", detail_html)
        self.assertNotIn("data-request-resubmitted-note-id", detail_html)

        request = RequestFactory().get("/")
        request.session = {"_freeipa_username": "reviewer"}

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            from core.templatetags.core_membership_notes import membership_notes_aggregate_for_user

            aggregate_html = str(
                membership_notes_aggregate_for_user(
                    {"request": request, "membership_can_view": True},
                    "alice",
                    compact=True,
                    next_url="/",
                )
            )

        self.assertIn("Request resubmitted", aggregate_html)
        self.assertNotIn("data-request-resubmitted-note-id", aggregate_html)

    def test_aggregate_profile_notes_preserve_resolved_and_unresolved_author_rendering(self) -> None:
        first_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        second_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
        )
        Note.objects.create(
            membership_request=first_request,
            username="committee2",
            content="First note",
            action={},
        )
        Note.objects.create(
            membership_request=second_request,
            username="ghost-user",
            content="Second note",
            action={},
        )

        request = RequestFactory().get("/")
        request.session = {"_freeipa_username": "reviewer"}

        with (
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                return_value={
                    "committee2": FreeIPAUser(
                        "committee2",
                        {
                            "uid": ["committee2"],
                            "displayname": ["Committee Two"],
                            "mail": ["committee2@example.com"],
                        },
                    ),
                },
            ) as lightweight_lookup_mock,
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.get",
                side_effect=AssertionError("aggregate profile notes must not use full-detail FreeIPAUser.get for author hydration"),
            ),
        ):
            from core.templatetags.core_membership_notes import membership_notes_aggregate_for_user

            html = str(
                membership_notes_aggregate_for_user(
                    {"request": request, "membership_can_view": True},
                    "alice",
                    compact=True,
                    next_url="/",
                )
            )

        self.assertIn("First note", html)
        self.assertIn("Second note", html)
        self.assertEqual(lightweight_lookup_mock.call_count, 1)
        self.assertEqual(set(lightweight_lookup_mock.call_args.args[0]), {"committee2", "ghost-user"})

        committee_marker = 'data-membership-notes-group-username="committee2"'
        committee_start = html.find(committee_marker)
        self.assertNotEqual(committee_start, -1)
        ghost_start = html.find('data-membership-notes-group-username="ghost-user"')
        self.assertNotEqual(ghost_start, -1)

        committee_group_html = html[committee_start:ghost_start]
        ghost_group_html = html[ghost_start:]

        self.assertIn("committee2", committee_group_html)
        self.assertIn('class="direct-chat-img img-circle"', committee_group_html)
        self.assertIn('alt="User Avatar"', committee_group_html)
        self.assertNotIn('alt="user image"', committee_group_html)

        self.assertIn("ghost-user", ghost_group_html)
        self.assertIn('alt="user image"', ghost_group_html)
        self.assertNotIn('class="direct-chat-img img-circle"', ghost_group_html)

    def test_aggregate_profile_notes_reuse_preloaded_target_and_avatar_safe_viewer(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="viewer",
            content="Viewer note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        target_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "displayname": ["Alice Example"],
                "mail": ["alice@example.com"],
            },
        )
        viewer_user = SimpleNamespace(
            username="viewer",
            email="viewer@example.com",
            is_authenticated=True,
            get_username=lambda: "viewer",
        )
        request = self._aggregate_request(viewer_user)

        with (
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                return_value={},
            ) as lightweight_lookup_mock,
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.get",
                side_effect=AssertionError("aggregate profile notes must keep route-known reuse inside the shared lightweight helper path"),
            ),
        ):
            from core.templatetags.core_membership_notes import membership_notes_aggregate_for_user

            html = str(
                membership_notes_aggregate_for_user(
                    {
                        "request": request,
                        "membership_can_view": True,
                        "membership_can_add": True,
                        "fu": target_user,
                    },
                    "alice",
                    compact=True,
                    next_url="/",
                )
            )

        self.assertEqual(lightweight_lookup_mock.call_count, 1)
        self.assertEqual(set(lightweight_lookup_mock.call_args.args[0]), {"ghost-user"})
        self.assertIn('name="aggregate_preloaded_target_token"', html)
        self.assertNotIn('aggregate_preloaded_target_username', html)
        self.assertNotIn('aggregate_preloaded_target_email', html)

        target_group_html = self._group_html(html, "alice", next_username="viewer")
        viewer_group_html = self._group_html(html, "viewer", next_username="ghost-user")
        ghost_group_html = self._group_html(html, "ghost-user")

        self.assertIn('class="direct-chat-img img-circle"', target_group_html)
        self.assertNotIn('alt="user image"', target_group_html)
        self.assertIn('class="direct-chat-img img-circle"', viewer_group_html)
        self.assertNotIn('alt="user image"', viewer_group_html)
        self.assertIn('alt="user image"', ghost_group_html)
        self.assertNotIn('class="direct-chat-img img-circle"', ghost_group_html)

    def test_aggregate_profile_notes_skip_username_only_viewer_reuse(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="viewer",
            content="Viewer note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        target_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "displayname": ["Alice Example"],
                "mail": ["alice@example.com"],
            },
        )
        viewer_from_lookup = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer Example"],
                "mail": ["viewer@example.com"],
            },
        )
        request = self._aggregate_request(
            SimpleNamespace(
                username="viewer",
                is_authenticated=True,
                get_username=lambda: "viewer",
            )
        )

        with (
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                return_value={"viewer": viewer_from_lookup},
            ) as lightweight_lookup_mock,
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.get",
                side_effect=AssertionError(
                    "aggregate profile notes must not widen request.user reuse with full-detail author fetches"
                ),
            ),
        ):
            from core.templatetags.core_membership_notes import membership_notes_aggregate_for_user

            html = str(
                membership_notes_aggregate_for_user(
                    {"request": request, "membership_can_view": True, "fu": target_user},
                    "alice",
                    compact=True,
                    next_url="/",
                )
            )

        self.assertEqual(lightweight_lookup_mock.call_count, 1)
        self.assertEqual(set(lightweight_lookup_mock.call_args.args[0]), {"ghost-user", "viewer"})

        target_group_html = self._group_html(html, "alice", next_username="viewer")
        viewer_group_html = self._group_html(html, "viewer", next_username="ghost-user")
        ghost_group_html = self._group_html(html, "ghost-user")

        self.assertIn('class="direct-chat-img img-circle"', target_group_html)
        self.assertNotIn('alt="user image"', target_group_html)
        self.assertIn('class="direct-chat-img img-circle"', viewer_group_html)
        self.assertNotIn('alt="user image"', viewer_group_html)
        self.assertIn('alt="user image"', ghost_group_html)
        self.assertNotIn('class="direct-chat-img img-circle"', ghost_group_html)

    def test_aggregate_profile_notes_reuse_evaluated_lazy_freeipa_viewer(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="viewer",
            content="Viewer note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        target_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "displayname": ["Alice Example"],
                "mail": ["alice@example.com"],
            },
        )
        viewer_user = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer Example"],
                "mail": ["viewer@example.com"],
            },
        )
        lazy_viewer_user = SimpleLazyObject(lambda: viewer_user)
        _ = lazy_viewer_user.username
        request = self._aggregate_request(lazy_viewer_user)

        with (
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                return_value={},
            ) as lightweight_lookup_mock,
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.get",
                side_effect=AssertionError(
                    "aggregate profile notes must keep evaluated lazy FreeIPA viewers on the shared lightweight helper path"
                ),
            ),
        ):
            from core.templatetags.core_membership_notes import membership_notes_aggregate_for_user

            html = str(
                membership_notes_aggregate_for_user(
                    {"request": request, "membership_can_view": True, "fu": target_user},
                    "alice",
                    compact=True,
                    next_url="/",
                )
            )

        self.assertEqual(lightweight_lookup_mock.call_count, 1)
        self.assertEqual(set(lightweight_lookup_mock.call_args.args[0]), {"ghost-user"})

        target_group_html = self._group_html(html, "alice", next_username="viewer")
        viewer_group_html = self._group_html(html, "viewer", next_username="ghost-user")
        ghost_group_html = self._group_html(html, "ghost-user")

        self.assertIn('class="direct-chat-img img-circle"', target_group_html)
        self.assertNotIn('alt="user image"', target_group_html)
        self.assertIn('class="direct-chat-img img-circle"', viewer_group_html)
        self.assertNotIn('alt="user image"', viewer_group_html)
        self.assertIn('alt="user image"', ghost_group_html)
        self.assertNotIn('class="direct-chat-img img-circle"', ghost_group_html)

    def test_aggregate_note_ajax_rerender_reuses_profile_target_user(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        lightweight_lookup_usernames: list[tuple[str, ...]] = []

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            lightweight_lookup_usernames.append(tuple(sorted(usernames)))
            return {}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
        ):
            response = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "aggregate_preloaded_target_token": self._aggregate_target_token(
                        username="alice",
                        email="alice@example.com",
                    ),
                    "note_action": "message",
                    "message": "Ajax note",
                    "compact": "1",
                    "next": "/user/alice/",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(lightweight_lookup_usernames, [("ghost-user",)])

        html = str(payload.get("html", ""))
        target_group_html = self._group_html(html, "alice", next_username="ghost-user")
        ghost_group_html = self._group_html(html, "ghost-user", next_username="reviewer")

        self.assertIn('class="direct-chat-img img-circle"', target_group_html)
        self.assertNotIn('alt="user image"', target_group_html)
        self.assertIn('alt="user image"', ghost_group_html)
        self.assertIn('name="aggregate_preloaded_target_token"', html)
        self.assertNotIn('aggregate_preloaded_target_username', html)
        self.assertNotIn('aggregate_preloaded_target_email', html)

    def test_aggregate_note_ajax_rerender_reuses_no_email_profile_target_from_signed_token(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        lightweight_lookup_usernames: list[tuple[str, ...]] = []

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            lightweight_lookup_usernames.append(tuple(sorted(usernames)))
            return {}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
        ):
            response = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "aggregate_preloaded_target_token": self._aggregate_target_token(username="alice"),
                    "note_action": "message",
                    "message": "Ajax note",
                    "compact": "1",
                    "next": "/user/alice/",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(lightweight_lookup_usernames, [("ghost-user",)])

        html = str(payload.get("html", ""))
        self.assertIn('data-membership-notes-group-username="alice"', html)
        self.assertIn('name="aggregate_preloaded_target_token"', html)
        self.assertNotIn('aggregate_preloaded_target_username', html)
        self.assertNotIn('aggregate_preloaded_target_email', html)

    def test_aggregate_note_ajax_rerender_ignores_untrusted_raw_target_identity_fields(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        lightweight_lookup_usernames: list[tuple[str, ...]] = []

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            lightweight_lookup_usernames.append(tuple(sorted(usernames)))
            return {}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
        ):
            response = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "aggregate_preloaded_target_username": "alice",
                    "aggregate_preloaded_target_email": "attacker@example.com",
                    "note_action": "message",
                    "message": "Ajax note",
                    "compact": "1",
                    "next": "/user/alice/",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(lightweight_lookup_usernames, [("alice", "ghost-user")])

        html = str(payload.get("html", ""))
        target_group_html = self._group_html(html, "alice", next_username="ghost-user")
        self.assertIn('alt="user image"', target_group_html)
        self.assertNotIn('class="direct-chat-img img-circle"', target_group_html)
        self.assertNotIn('aggregate_preloaded_target_username', html)
        self.assertNotIn('aggregate_preloaded_target_email', html)

    def test_detail_notes_do_not_switch_to_aggregate_lightweight_author_lookup(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=req,
            username="committee2",
            content="Detail note",
            action={},
        )

        request = RequestFactory().get("/")
        request.session = {"_freeipa_username": "reviewer"}

        reviewer = self._reviewer_user()
        committee2 = FreeIPAUser(
            "committee2",
            {
                "uid": ["committee2"],
                "displayname": ["Committee Two"],
                "mail": ["committee2@example.com"],
            },
        )

        def _fake_get(username: str) -> FreeIPAUser | None:
            if username == "committee2":
                return committee2
            if username == "reviewer":
                return reviewer
            return None

        with (
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=AssertionError("detail notes must stay on the existing full-detail author path"),
            ),
            patch("core.templatetags.core_membership_notes.FreeIPAUser.get", side_effect=_fake_get),
        ):
            from core.templatetags.core_membership_notes import membership_notes

            html = str(
                membership_notes(
                    {
                        "request": request,
                        "membership_can_add": True,
                        "membership_can_change": True,
                        "membership_can_delete": True,
                    },
                    req,
                    compact=True,
                    next_url="/",
                )
            )

        self.assertIn("Detail note", html)
