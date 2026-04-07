
import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
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
