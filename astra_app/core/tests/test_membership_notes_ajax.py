
import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_MEMBERSHIP


class MembershipNotesAjaxTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

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

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
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
