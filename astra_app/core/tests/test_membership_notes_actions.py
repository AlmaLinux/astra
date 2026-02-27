
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from post_office.models import Email

from core.membership_request_workflow import (
    approve_membership_request,
    ignore_membership_request,
    record_membership_request_created,
    reject_membership_request,
)
from core.models import MembershipRequest, MembershipType, MembershipTypeCategory, Note, Organization
from core.tests.utils_test_data import ensure_email_templates


class MembershipNotesActionTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_email_templates()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
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

    def test_request_created_records_action_note(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        record_membership_request_created(
            membership_request=req,
            actor_username="alice",
            send_submitted_email=False,
        )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="alice",
                action={"type": "request_created"},
            ).exists()
        )

    def test_request_created_records_email_note_when_sent(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            cc="",
            bcc="",
            subject="Submitted",
            message="Submitted text",
            html_message="",
        )

        class _Target:
            username = "alice"
            email = "alice@example.com"
            first_name = "Alice"
            last_name = "User"
            full_name = "Alice User"

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=_Target()),
            patch("core.membership_request_workflow.queue_templated_email", return_value=email),
        ):
            record_membership_request_created(
                membership_request=req,
                actor_username="alice",
                send_submitted_email=True,
            )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="alice",
                action__type="contacted",
                action__kind="submitted",
                action__email_id=email.id,
            ).exists()
        )

    def test_request_created_action_label_includes_creator(self) -> None:
        from core.membership_notes import note_action_label

        self.assertEqual(
            note_action_label({"type": "request_created"}),
            "Request created",
        )

    def test_request_approved_records_action_note(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        class _Target:
            username = "alice"
            email = ""

            def add_to_group(self, *, group_name: str) -> None:  # noqa: ARG002
                return

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=_Target()),
            patch("core.membership_request_workflow.queue_templated_email"),
        ):
            approve_membership_request(
                membership_request=req,
                actor_username="reviewer",
                send_approved_email=False,
            )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "request_approved"},
            ).exists()
        )

    def test_request_approved_records_email_note_when_sent(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            cc="",
            bcc="",
            subject="Approved",
            message="Approved text",
            html_message="",
        )

        class _Target:
            username = "alice"
            email = "alice@example.com"
            first_name = "Alice"
            last_name = "User"
            full_name = "Alice User"

            def add_to_group(self, *, group_name: str) -> None:  # noqa: ARG002
                return

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=_Target()),
            patch("core.membership_request_workflow.queue_templated_email", return_value=email),
        ):
            approve_membership_request(
                membership_request=req,
                actor_username="reviewer",
                send_approved_email=True,
            )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="contacted",
                action__kind="approved",
                action__email_id=email.id,
            ).exists()
        )

    def test_request_rejected_records_action_note(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=None),
            patch("core.membership_request_workflow.queue_templated_email"),
        ):
            reject_membership_request(
                membership_request=req,
                actor_username="reviewer",
                rejection_reason="Nope",
                send_rejected_email=False,
            )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "request_rejected"},
            ).exists()
        )

    def test_request_rejected_records_email_note_when_sent(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            cc="",
            bcc="",
            subject="Rejected",
            message="Rejected text",
            html_message="",
        )

        class _Target:
            username = "alice"
            email = "alice@example.com"
            first_name = "Alice"
            last_name = "User"
            full_name = "Alice User"

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=_Target()),
            patch("core.membership_request_workflow.queue_templated_email", return_value=email),
        ):
            reject_membership_request(
                membership_request=req,
                actor_username="reviewer",
                rejection_reason="Nope",
                send_rejected_email=True,
            )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="contacted",
                action__kind="rejected",
                action__email_id=email.id,
            ).exists()
        )

    def test_request_ignored_records_action_note(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        ignore_membership_request(membership_request=req, actor_username="reviewer")

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "request_ignored"},
            ).exists()
        )

    def test_org_request_created_sends_submitted_email_to_representative(self) -> None:
        organization = Organization.objects.create(
            name="Acme",
            representative="org-rep",
            business_contact_email="fallback@example.com",
        )
        req = MembershipRequest.objects.create(
            requested_organization=organization,
            membership_type_id="individual",
        )

        rep = type(
            "_Rep",
            (),
            {
                "username": "org-rep",
                "email": "rep@example.com",
                "first_name": "Org",
                "last_name": "Rep",
                "full_name": "Org Rep",
            },
        )()

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=rep),
            patch("core.membership_request_workflow.queue_templated_email") as send_mail,
        ):
            record_membership_request_created(
                membership_request=req,
                actor_username="reviewer",
                send_submitted_email=True,
            )

        send_mail.assert_called_once()
        _args, kwargs = send_mail.call_args
        self.assertEqual(kwargs.get("recipients"), ["rep@example.com"])
        self.assertEqual(kwargs.get("template_name"), settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME)

    def test_org_request_created_falls_back_to_primary_contact_when_representative_email_missing(self) -> None:
        organization = Organization.objects.create(
            name="Acme",
            representative="org-rep",
            business_contact_email="fallback@example.com",
        )
        req = MembershipRequest.objects.create(
            requested_organization=organization,
            membership_type_id="individual",
        )

        rep = type(
            "_Rep",
            (),
            {
                "username": "org-rep",
                "email": "",
                "first_name": "",
                "last_name": "",
                "full_name": "",
            },
        )()

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=rep),
            patch("core.membership_request_workflow.queue_templated_email") as send_mail,
        ):
            record_membership_request_created(
                membership_request=req,
                actor_username="reviewer",
                send_submitted_email=True,
            )

        send_mail.assert_called_once()
        _args, kwargs = send_mail.call_args
        self.assertEqual(kwargs.get("recipients"), ["fallback@example.com"])
        self.assertEqual(kwargs.get("template_name"), settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME)

    def test_org_request_created_falls_back_to_primary_contact_when_representative_lookup_fails(self) -> None:
        organization = Organization.objects.create(
            name="Acme",
            representative="org-rep",
            business_contact_email="fallback@example.com",
        )
        req = MembershipRequest.objects.create(
            requested_organization=organization,
            membership_type_id="individual",
        )

        with (
            patch("core.membership_request_workflow.FreeIPAUser.get", side_effect=RuntimeError("ipa down")),
            patch("core.membership_request_workflow.queue_templated_email") as send_mail,
        ):
            record_membership_request_created(
                membership_request=req,
                actor_username="reviewer",
                send_submitted_email=True,
            )

        send_mail.assert_called_once()
        _args, kwargs = send_mail.call_args
        self.assertEqual(kwargs.get("recipients"), ["fallback@example.com"])
        self.assertEqual(kwargs.get("template_name"), settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME)


class MembershipReopenNoteTests(TestCase):
    """Tests for the reopen audit note creation and rendering."""

    def setUp(self) -> None:
        super().setUp()
        ensure_email_templates()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
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

    def test_reopen_ignored_request_records_action_note(self) -> None:
        from core.membership_request_workflow import reopen_ignored_membership_request

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.ignored,
        )

        reopen_ignored_membership_request(membership_request=req, actor_username="reviewer")

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "request_reopened"},
            ).exists()
        )

    def test_request_reopened_action_type_has_display_label(self) -> None:
        from core.membership_notes import note_action_icon, note_action_label

        action = {"type": "request_reopened"}
        label = note_action_label(action)
        icon = note_action_icon(action)
        self.assertIn("reopen", label.lower())
        self.assertNotEqual(icon, "fa-bolt")  # should be a specific icon, not the fallback
