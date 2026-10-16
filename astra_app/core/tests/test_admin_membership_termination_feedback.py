from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import MembershipLog, MembershipTerminationFeedback, MembershipType
from core.tests.utils_test_data import ensure_core_categories


class AdminMembershipTerminationFeedbackTests(TestCase):
    """Admins can read why members left, but nobody can rewrite or remove that record."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "group_cn": "almalinux-individual", "category_id": "individual", "enabled": True},
        )
        log = MembershipLog.objects.create(
            actor_username="bob",
            target_username="bob",
            membership_type=individual,
            action=MembershipLog.Action.terminated,
        )
        self.feedback = MembershipTerminationFeedback.objects.create(
            membership_log=log,
            username="bob",
            membership_type=individual,
            reason_category=MembershipTerminationFeedback.ReasonCategory.no_longer_needed,
            reason_text="Moving to another distribution",
        )
        session = self.client.session
        session["_freeipa_username"] = "alice"
        session.save()
        patcher = patch(
            "core.freeipa.user.FreeIPAUser.get",
            return_value=FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_admin_can_list_and_read_feedback(self) -> None:
        changelist = self.client.get(reverse("admin:core_membershipterminationfeedback_changelist"))
        self.assertContains(changelist, "bob")
        self.assertContains(changelist, "No longer needed")

        detail = self.client.get(reverse("admin:core_membershipterminationfeedback_change", args=[self.feedback.pk]))
        self.assertContains(detail, "Moving to another distribution")

    def test_feedback_cannot_be_added_changed_or_deleted(self) -> None:
        self.assertEqual(self.client.get(reverse("admin:core_membershipterminationfeedback_add")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("admin:core_membershipterminationfeedback_delete", args=[self.feedback.pk]), {"post": "yes"}
            ).status_code,
            403,
        )
        self.client.post(
            reverse("admin:core_membershipterminationfeedback_change", args=[self.feedback.pk]),
            {"reason_text": "Rewritten"},
        )

        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.reason_text, "Moving to another distribution")
