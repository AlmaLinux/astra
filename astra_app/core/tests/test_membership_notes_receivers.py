from typing import override
from unittest.mock import patch

from django.test import TestCase

from core import signals as astra_signals
from core.country_codes import country_label_from_code
from core.membership_notes import CUSTOS
from core.membership_notes import add_note as add_note_real
from core.models import IPAUser, MembershipRequest, MembershipType, MembershipTypeCategory, Note


class MembershipNotesReceiversTests(TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
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
        MembershipType.objects.update_or_create(
            code="individual_alt",
            defaults={
                "name": "Individual Alt",
                "group_cn": "almalinux-individual-alt",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

    def test_connect_membership_notes_receivers_is_idempotent(self) -> None:
        from core import membership_notes_receivers

        membership_notes_receivers.connect_membership_notes_receivers()
        membership_notes_receivers.connect_membership_notes_receivers()

        matching_receivers = [
            receiver_entry
            for receiver_entry in astra_signals.user_country_changed.receivers
            if receiver_entry[0][0] == "core.membership_notes_receivers.user_country_changed"
        ]

        self.assertEqual(len(matching_receivers), 1)

    def test_country_change_signal_with_no_pending_requests_creates_no_notes(self) -> None:
        from core import membership_notes_receivers

        membership_notes_receivers.connect_membership_notes_receivers()

        user = IPAUser(username="nobody")

        with patch("core.mattermost_webhooks.dispatch_mattermost_event", autospec=True):
            astra_signals.user_country_changed.send(
                sender=user.__class__,
                username=user.username,
                old_country="US",
                new_country="FR",
                actor=user.username,
            )

        self.assertEqual(
            Note.objects.count(),
            0,
        )

    def test_country_change_signal_creates_notes_for_pending_requests(self) -> None:
        from core import membership_notes_receivers

        membership_notes_receivers.connect_membership_notes_receivers()

        first = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        second = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual_alt")
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
        )

        with patch("core.mattermost_webhooks.dispatch_mattermost_event", autospec=True):
            astra_signals.user_country_changed.send(
                sender=self.__class__,
                username="alice",
                old_country="US",
                new_country="FR",
                actor="alice",
            )

        expected = (
            f"alice updated their country from {country_label_from_code('US')} "
            f"to {country_label_from_code('FR')}."
        )
        notes = list(Note.objects.filter(membership_request_id__in=[first.pk, second.pk]).order_by("pk"))
        self.assertEqual(len(notes), 2)
        self.assertEqual([note.username for note in notes], [CUSTOS, CUSTOS])
        self.assertEqual([note.content for note in notes], [expected, expected])

    def test_country_change_note_failure_logs_and_continues(self) -> None:
        from core import membership_notes_receivers

        membership_notes_receivers.connect_membership_notes_receivers()

        first = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        second = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual_alt")

        def _maybe_fail(*, membership_request: MembershipRequest, username: str, content: str):
            if membership_request.pk == first.pk:
                raise RuntimeError("note write failed")
            return add_note_real(membership_request=membership_request, username=username, content=content)

        with (
            patch("core.membership_notes_receivers.add_note", side_effect=_maybe_fail),
            patch("core.mattermost_webhooks.dispatch_mattermost_event", autospec=True),
            self.assertLogs("core.membership_notes_receivers", level="ERROR") as captured,
        ):
            astra_signals.user_country_changed.send(
                sender=self.__class__,
                username="alice",
                old_country="US",
                new_country="FR",
                actor="alice",
            )

        self.assertTrue(captured.records)
        self.assertTrue(
            any(record.getMessage() == "membership_request.note.error" for record in captured.records),
        )
        self.assertEqual(Note.objects.filter(membership_request=first).count(), 0)
        self.assertEqual(Note.objects.filter(membership_request=second, username=CUSTOS).count(), 1)
