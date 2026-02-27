
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from post_office.models import STATUS, Email, EmailTemplate, Log

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

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "View email")
        self.assertContains(resp, "Approval notice")
        self.assertContains(resp, "alice@example.com")
        self.assertContains(resp, "committee@example.com")
        self.assertContains(resp, "HTML body")
        self.assertContains(resp, "Plain text body")
        self.assertContains(resp, "sent")

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

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sent subject")
        self.assertContains(resp, "Sent html body")
        self.assertContains(resp, "Sent text body")
        self.assertNotContains(resp, "Template subject")
        self.assertNotContains(resp, "Template html")
        self.assertNotContains(resp, "Template text")
