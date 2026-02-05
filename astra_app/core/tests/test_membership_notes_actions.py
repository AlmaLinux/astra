from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from post_office.models import Email

from core.membership_request_workflow import (
    approve_membership_request,
    ignore_membership_request,
    record_membership_request_created,
    reject_membership_request,
)
from core.models import MembershipRequest, MembershipType, Note


class MembershipNotesActionTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
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
            patch("core.membership_request_workflow.post_office.mail.send"),
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
            patch("core.membership_request_workflow.post_office.mail.send", return_value=email),
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
            patch("core.membership_request_workflow.post_office.mail.send"),
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
            patch("core.membership_request_workflow.post_office.mail.send", return_value=email),
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
