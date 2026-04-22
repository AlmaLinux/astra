
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from post_office.models import STATUS, Email, EmailTemplate, Log, RecipientDeliveryStatus

from core.freeipa.user import FreeIPAUser
from core.membership_notes import add_note
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipRequestEmailModalTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
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

    def _detail_payload(self, request_pk: int) -> dict[str, object]:
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[request_pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        return response.json()

    def _first_contacted_email(self, request_pk: int) -> dict[str, object]:
        payload = self._detail_payload(request_pk)

        for group in payload["groups"]:
            for entry in group["entries"]:
                contacted_email = entry.get("contacted_email")
                if contacted_email is not None:
                    return contacted_email

        self.fail("Expected contacted_email detail in notes payload.")

    def test_membership_notes_render_email_modal(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            cc="cc@example.com",
            bcc="bcc@example.com",
            subject="Approval notice",
            message="Plain text body",
            html_message="<p>HTML body</p>",
            headers={"Reply-To": "committee@example.com"},
        )
        Log.objects.create(
            email=email,
            status=STATUS.sent,
            message="sent",
            exception_type="",
        )

        add_note(
            membership_request=req,
            username="reviewer",
            action={"type": "contacted", "kind": "approved", "email_id": email.id},
        )

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()),
            patch(
                "core.templatetags.core_membership_notes.Note.objects.filter",
                side_effect=AssertionError("legacy direct note read should not run"),
            ),
        ):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-membership-request-notes-root")
        self.assertContains(resp, f'data-membership-request-id="{req.pk}"')
        self.assertContains(resp, reverse("api-membership-request-notes-summary", args=[req.pk]))
        self.assertContains(resp, reverse("api-membership-request-notes", args=[req.pk]))
        self.assertContains(resp, reverse("api-membership-request-notes-add", args=[req.pk]))
        self.assertNotContains(resp, "Approval email sent")
        self.assertNotContains(resp, "View email")
        self.assertNotContains(resp, "Approval notice")
        self.assertNotContains(resp, "HTML body")
        self.assertNotContains(resp, "Plain text body")

        contacted_email = self._first_contacted_email(req.pk)
        self.assertEqual(contacted_email["subject"], "Approval notice")
        self.assertEqual(contacted_email["to"], ["alice@example.com"])
        self.assertEqual(contacted_email["cc"], ["cc@example.com"])
        self.assertEqual(contacted_email["bcc"], ["bcc@example.com"])
        self.assertEqual(contacted_email["reply_to"], "committee@example.com")
        self.assertEqual(contacted_email["html"], "<p>HTML body</p>")
        self.assertEqual(contacted_email["text"], "Plain text body")
        self.assertEqual(contacted_email["logs"][0]["status"], "sent")

    def test_modal_uses_sent_email_contents_over_template(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        template = EmailTemplate.objects.create(
            name="approval-template",
            subject="Template subject {{ username }}",
            content="Template text {{ username }}",
            html_content="<p>Template html {{ username }}</p>",
        )

        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            cc="",
            bcc="",
            subject="Sent subject",
            message="Sent text body",
            html_message="<p>Sent html body</p>",
            template=template,
            context={"username": "alice"},
        )
        Log.objects.create(
            email=email,
            status=STATUS.sent,
            message="sent",
            exception_type="",
        )

        add_note(
            membership_request=req,
            username="reviewer",
            action={"type": "contacted", "kind": "approved", "email_id": email.id},
        )

        contacted_email = self._first_contacted_email(req.pk)

        self.assertEqual(contacted_email["subject"], "Sent subject")
        self.assertEqual(contacted_email["html"], "<p>Sent html body</p>")
        self.assertEqual(contacted_email["text"], "Sent text body")
        self.assertNotEqual(contacted_email["subject"], "Template subject alice")
        self.assertNotIn("Template html", contacted_email["html"])
        self.assertNotIn("Template text", contacted_email["text"])

    def test_membership_notes_render_aggregate_recipient_delivery_summary(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com,bob@example.com",
            subject="Approval notice",
            message="Plain text body",
            html_message="<p>HTML body</p>",
            recipient_delivery_status=RecipientDeliveryStatus.DELIVERED,
        )
        Log.objects.create(
            email=email,
            status=STATUS.sent,
            message="sent",
            exception_type="",
        )

        add_note(
            membership_request=req,
            username="reviewer",
            action={"type": "contacted", "kind": "approved", "email_id": email.id},
        )

        contacted_email = self._first_contacted_email(req.pk)

        self.assertEqual(contacted_email["recipient_delivery_summary"], "Delivered")
        self.assertEqual(
            contacted_email["recipient_delivery_summary_note"],
            "Single rolled-up status across all recipients. Individual recipient outcomes may differ.",
        )

    def test_membership_notes_render_empty_recipient_delivery_summary(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            subject="Approval notice",
            message="Plain text body",
            html_message="<p>HTML body</p>",
            recipient_delivery_status=None,
        )
        Log.objects.create(
            email=email,
            status=STATUS.sent,
            message="sent",
            exception_type="",
        )

        add_note(
            membership_request=req,
            username="reviewer",
            action={"type": "contacted", "kind": "approved", "email_id": email.id},
        )

        contacted_email = self._first_contacted_email(req.pk)

        self.assertEqual(contacted_email["recipient_delivery_summary"], "No aggregate recipient status recorded.")
        self.assertEqual(contacted_email["recipient_delivery_summary_note"], "")
