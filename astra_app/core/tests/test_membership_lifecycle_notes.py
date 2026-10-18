import datetime
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_notes import note_action_icon, note_action_label
from core.models import FreeIPAPermissionGrant, Membership, MembershipLog, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.tests.test_membership_dues import MembershipDuesTestMixin


class LifecycleNotesTestMixin(MembershipDuesTestMixin):
    def _active_individual(self, username: str = "rep1") -> Membership:
        from core.membership_request_workflow import create_approved_membership_request

        self.individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "group_cn": "almalinux-individual", "category_id": "individual", "enabled": True},
        )
        membership_request = create_approved_membership_request(
            actor_username="committee",
            membership_type=self.individual,
            decided_at=timezone.now(),
            target_username=username,
        )
        MembershipLog.create_for_approval(
            actor_username="committee",
            membership_type=self.individual,
            target_username=username,
            membership_request=membership_request,
        )
        return Membership.objects.get(target_username=username, membership_type=self.individual)

    def _notes(self, action_type: str) -> list[Note]:
        return [note for note in Note.objects.order_by("pk") if (note.action or {}).get("type") == action_type]


class LifecycleNoteTests(LifecycleNotesTestMixin, TestCase):
    """Expiry changes and terminations leave a committee note on the term's request."""

    def test_sponsorship_expiry_change_notes_old_and_new_dates_with_the_reason(self) -> None:
        membership = self._active_gold()
        term_request = MembershipRequest.objects.get(requested_organization=self.organization)
        new_expires_at = datetime.datetime(2031, 1, 31, 23, 59, 59, tzinfo=datetime.UTC)

        MembershipLog.create_for_expiry_change(
            actor_username="committee",
            membership_type=self.gold,
            expires_at=new_expires_at,
            target_organization=self.organization,
            reason="Grace period agreed with the sponsor",
        )

        [note] = self._notes("expiry_changed")
        self.assertEqual(note.membership_request, term_request)
        self.assertEqual(note.username, "committee")
        self.assertEqual(note.content, "Grace period agreed with the sponsor")
        self.assertEqual(
            note.action,
            {
                "type": "expiry_changed",
                "membership_type": "gold",
                "old_expires_at": membership.expires_at.isoformat(),
                "new_expires_at": new_expires_at.isoformat(),
            },
        )

    def test_individual_expiry_change_without_a_reason_notes_only_the_action(self) -> None:
        membership = self._active_individual()

        MembershipLog.create_for_expiry_change(
            actor_username="committee",
            membership_type=self.individual,
            expires_at=membership.expires_at + datetime.timedelta(days=10),
            target_username="rep1",
        )

        [note] = self._notes("expiry_changed")
        self.assertEqual(note.membership_request, MembershipRequest.objects.get(requested_username="rep1"))
        self.assertIsNone(note.content)
        self.assertEqual(note.action["old_expires_at"], membership.expires_at.isoformat())

    def test_expiry_change_to_the_current_date_is_rejected(self) -> None:
        membership = self._active_gold()
        logs_before = MembershipLog.objects.count()

        with self.assertRaises(ValidationError):
            MembershipLog.create_for_expiry_change(
                actor_username="committee",
                membership_type=self.gold,
                expires_at=membership.expires_at,
                target_organization=self.organization,
            )

        self.assertEqual(MembershipLog.objects.count(), logs_before)
        self.assertEqual(self._notes("expiry_changed"), [])

    def test_termination_note_outlives_the_membership_and_the_organization(self) -> None:
        membership = self._active_gold()

        MembershipLog.create_for_termination(
            actor_username="committee",
            membership_type=self.gold,
            target_organization=self.organization,
        )
        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())
        self.organization.delete()

        [note] = self._notes("terminated")
        self.assertEqual(note.username, "committee")
        self.assertIsNone(note.content)
        self.assertEqual(
            note.action,
            {"type": "terminated", "membership_type": "gold", "expires_at": membership.expires_at.isoformat()},
        )

    def test_self_termination_note_records_the_reason_category(self) -> None:
        self._active_individual()

        MembershipLog.create_for_termination(
            actor_username="rep1",
            membership_type=self.individual,
            target_username="rep1",
            reason_category="no_longer_needed",
        )

        [note] = self._notes("terminated")
        self.assertEqual(note.username, "rep1")
        self.assertEqual(note.action["reason_category"], "no_longer_needed")


class LifecycleNoteLabelTests(TestCase):
    def test_labels_show_dates_and_the_member_reason(self) -> None:
        for action, expected in (
            (
                {
                    "type": "expiry_changed",
                    "old_expires_at": "2026-10-01T23:59:59+00:00",
                    "new_expires_at": "2027-10-01T23:59:59+00:00",
                },
                "Expiration changed from 2026-10-01 to 2027-10-01",
            ),
            (
                {"type": "expiry_changed", "old_expires_at": None, "new_expires_at": "2027-10-01T23:59:59+00:00"},
                "Expiration set to 2027-10-01",
            ),
            (
                {"type": "terminated", "expires_at": "2027-10-01T23:59:59+00:00"},
                "Membership terminated (was due to expire 2027-10-01)",
            ),
            ({"type": "terminated", "expires_at": None}, "Membership terminated"),
            (
                {"type": "terminated", "expires_at": None, "reason_category": "no_longer_needed"},
                "Membership terminated by member (No longer needed)",
            ),
        ):
            with self.subTest(action=action):
                self.assertEqual(note_action_label(action), expected)

        self.assertEqual(note_action_icon({"type": "expiry_changed"}), "fa-calendar-pen")
        self.assertEqual(note_action_icon({"type": "terminated"}), "fa-user-slash")


class LifecycleNoteViewTests(LifecycleNotesTestMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        for permission in (ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="committee",
            )
        users = {
            username: FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})
            for username in ("committee", "rep1")
        }
        patcher = patch("core.freeipa.user.FreeIPAUser.get", side_effect=lambda username, **_kwargs: users.get(username))
        patcher.start()
        self.addCleanup(patcher.stop)
        session = self.client.session
        session["_freeipa_username"] = "committee"
        session.save()

    def _messages(self, response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_committee_expiry_reason_becomes_the_note_content(self) -> None:
        # One endpoint serves sponsorships and individual memberships alike.
        for membership in (self._active_gold(), self._active_individual()):
            with self.subTest(membership=membership):
                self.client.post(
                    reverse("membership-update", args=[membership.pk]),
                    data={"expires_on": "2031-01-31", "expiration_reason": "  Paid late  "},
                )

        self.assertEqual([note.content for note in self._notes("expiry_changed")], ["Paid late", "Paid late"])

    def test_committee_expiry_change_to_the_current_date_records_nothing(self) -> None:
        membership = self._active_gold()
        # The form sets an end-of-day UTC date, so give the membership one.
        Membership.objects.filter(pk=membership.pk).update(
            expires_at=datetime.datetime(2031, 1, 31, 23, 59, 59, tzinfo=datetime.UTC)
        )
        logs_before = MembershipLog.objects.count()

        response = self.client.post(
            reverse("membership-update", args=[membership.pk]),
            data={"expires_on": "2031-01-31"},
        )

        self.assertIn("No changes to save.", self._messages(response))
        self.assertEqual(MembershipLog.objects.count(), logs_before)


class AutoIgnoreDeletedUsersTests(TestCase):
    def test_requests_of_users_gone_from_freeipa_are_ignored_by_custos(self) -> None:
        from core.membership import auto_ignore_pending_requests_for_deleted_users
        from core.membership_notes import CUSTOS
        from core.tests.utils_test_data import ensure_core_categories

        ensure_core_categories()
        individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "group_cn": "almalinux-individual", "category_id": "individual", "enabled": True},
        )
        ghost_request = MembershipRequest.objects.create(requested_username="ghost", membership_type=individual)
        MembershipRequest.objects.create(requested_username="alice", membership_type=individual)

        with patch("core.membership.FreeIPAUser.all", return_value=[FreeIPAUser("alice", {"uid": ["alice"]})]):
            self.assertEqual(auto_ignore_pending_requests_for_deleted_users(), 1)

        # Nobody decided this; it shows as the system, like every other automatic entry.
        ghost_request.refresh_from_db()
        self.assertEqual(ghost_request.decided_by_username, CUSTOS)
        self.assertEqual(
            list(MembershipLog.objects.values_list("membership_request_id", "action", "actor_username")),
            [(ghost_request.pk, MembershipLog.Action.ignored, CUSTOS)],
        )
        self.assertEqual(list(Note.objects.values_list("membership_request_id", "username")), [(ghost_request.pk, CUSTOS)])
