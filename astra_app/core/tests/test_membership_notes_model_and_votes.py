
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.membership_notes import tally_last_votes
from core.models import MembershipRequest, MembershipType, Note


class MembershipNotesModelTests(TestCase):
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

    def test_note_requires_content_or_action(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        note = Note(membership_request=req, username="reviewer", content=None, action=None)
        with self.assertRaises(ValidationError):
            note.full_clean()

    def test_note_allows_action_without_content(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        note = Note(
            membership_request=req,
            username="reviewer",
            content=None,
            action={"type": "vote", "value": "approve"},
        )
        note.full_clean()
        note.save()

        self.assertIsNotNone(note.pk)

    def test_tally_last_votes_counts_only_latest_per_user(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        Note.objects.create(
            membership_request=req,
            username="reviewer1",
            content=None,
            action={"type": "vote", "value": "approve"},
        )
        Note.objects.create(
            membership_request=req,
            username="reviewer2",
            content=None,
            action={"type": "vote", "value": "approve"},
        )
        Note.objects.create(
            membership_request=req,
            username="reviewer1",
            content=None,
            action={"type": "vote", "value": "disapprove"},
        )

        approvals, disapprovals = tally_last_votes(Note.objects.filter(membership_request=req))
        self.assertEqual(approvals, 1)
        self.assertEqual(disapprovals, 1)
