import io
import json

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import MembershipRequest, MembershipType, Note
from core.membership_request_repairs import reset_rejected_membership_request_to_pending


class ResetRejectedMembershipRequestTests(TestCase):
    def _membership_type(self) -> MembershipType:
        membership_type, _created = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        return membership_type

    def test_apply_clears_decision_fields_and_trims_trailing_rejection_reason(self) -> None:
        membership_type = self._membership_type()
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.rejected,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
            responses=[
                {"Contributions": "Old"},
                {"Rejection reason": "No."},
            ],
        )

        result = reset_rejected_membership_request_to_pending(
            membership_request=membership_request,
            actor_username="alex",
            note_content="Resetting due to email bug",
            apply_changes=True,
        )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)
        self.assertIsNone(membership_request.on_hold_at)
        self.assertIsNone(membership_request.decided_at)
        self.assertEqual(membership_request.decided_by_username, "")
        self.assertEqual(membership_request.responses, [{"Contributions": "Old"}])
        self.assertTrue(result.trimmed_rejection_reason)
        self.assertFalse(result.dry_run)

        note = Note.objects.get(membership_request=membership_request, username="alex")
        self.assertEqual(note.content, "Resetting due to email bug")

    def test_apply_preserves_non_trailing_rejection_reason_rows(self) -> None:
        membership_type = self._membership_type()
        responses = [
            {"Rejection reason": "Earlier"},
            {"Contributions": "Old"},
        ]
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.rejected,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
            responses=responses,
        )

        result = reset_rejected_membership_request_to_pending(
            membership_request=membership_request,
            actor_username="alex",
            note_content="Resetting due to email bug",
            apply_changes=True,
        )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.responses, responses)
        self.assertFalse(result.trimmed_rejection_reason)

    def test_apply_rejects_non_rejected_requests(self) -> None:
        membership_type = self._membership_type()
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
            responses=[{"Contributions": "Old"}],
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Only rejected requests can be reset to pending",
        ):
            reset_rejected_membership_request_to_pending(
                membership_request=membership_request,
                actor_username="alex",
                note_content="Resetting due to email bug",
                apply_changes=True,
            )


class MembershipRequestRepairCommandTests(TestCase):
    def _membership_type(self) -> MembershipType:
        membership_type, _created = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        return membership_type

    def test_apply_requires_actor_and_reason(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "--apply requires both --actor and --reason.",
        ):
            call_command(
                "membership_request_repair",
                "--request-id",
                "1",
                "--reset-to-pending",
                "--apply",
            )

    def test_dry_run_reports_without_mutating(self) -> None:
        membership_type = self._membership_type()
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.rejected,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
            responses=[
                {"Contributions": "Old"},
                {"Rejection reason": "No."},
            ],
        )

        stdout = io.StringIO()
        call_command(
            "membership_request_repair",
            "--request-id",
            str(membership_request.pk),
            "--reset-to-pending",
            "--dry-run",
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["request_id"], membership_request.pk)
        self.assertEqual(payload["from_status"], MembershipRequest.Status.rejected)
        self.assertEqual(payload["to_status"], MembershipRequest.Status.pending)
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["trimmed_rejection_reason"])

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.rejected)
        self.assertEqual(len(membership_request.responses), 2)
        self.assertFalse(Note.objects.filter(membership_request=membership_request).exists())

    def test_apply_resets_request_and_records_note(self) -> None:
        membership_type = self._membership_type()
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.rejected,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
            responses=[
                {"Contributions": "Old"},
                {"Rejection reason": "No."},
            ],
        )

        stdout = io.StringIO()
        call_command(
            "membership_request_repair",
            "--request-id",
            str(membership_request.pk),
            "--reset-to-pending",
            "--apply",
            "--actor",
            "alex",
            "--reason",
            "Resetting due to email bug",
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["dry_run"])
        self.assertTrue(payload["note_created"])

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)
        self.assertEqual(membership_request.responses, [{"Contributions": "Old"}])
        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                username="alex",
                content="Resetting due to email bug",
            ).exists()
        )