import re
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import AccountDeletionRequest, MembershipLog, MembershipTerminationFeedback, MembershipType
from core.tests.utils_test_data import ensure_core_categories


class AdminMenuBadgeTests(TestCase):
    """The admin sidebar counts the self-service records that still need an admin's attention."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        session = self.client.session
        session["_freeipa_username"] = "alice"
        session.save()
        patcher = patch(
            "core.freeipa.user.FreeIPAUser.get",
            return_value=FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sidebar_badge(self, changelist_url_name: str) -> str | None:
        html = self.client.get(reverse("admin:index")).content.decode()
        sidebar = html[html.index('id="jazzy-sidebar"'):]
        link = re.search(rf'href="{re.escape(reverse(changelist_url_name))}".*?</a>', sidebar, re.DOTALL)
        self.assertIsNotNone(link)
        badge = re.search(r'<span class="badge[^"]*"[^>]*>(\d+)</span>', link.group(0))
        return None if badge is None else badge.group(1)

    def test_badges_are_hidden_when_nothing_needs_attention(self) -> None:
        self.assertIsNone(self._sidebar_badge("admin:core_accountdeletionrequest_changelist"))
        self.assertIsNone(self._sidebar_badge("admin:core_membershipterminationfeedback_changelist"))

    def test_badge_counts_pending_account_deletion_requests(self) -> None:
        for username, status in (
            ("bob", AccountDeletionRequest.Status.pending_review),
            ("carol", AccountDeletionRequest.Status.pending_privilege_check),
            ("dave", AccountDeletionRequest.Status.completed),
            ("erin", AccountDeletionRequest.Status.cancelled),
        ):
            AccountDeletionRequest.objects.create(
                username=username, status=status, reason_category=AccountDeletionRequest.ReasonCategory.other
            )

        self.assertEqual(self._sidebar_badge("admin:core_accountdeletionrequest_changelist"), "2")

    def test_badge_counts_termination_feedback_whose_reason_is_not_purged_yet(self) -> None:
        individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "group_cn": "almalinux-individual", "category_id": "individual", "enabled": True},
        )
        for username, reason_text, cleared_at in (
            ("bob", "Moving to another distribution", None),
            ("carol", "", timezone.now()),  # already purged
            ("dave", "", None),  # gave no free text, nothing to purge
        ):
            MembershipTerminationFeedback.objects.create(
                membership_log=MembershipLog.objects.create(
                    actor_username=username,
                    target_username=username,
                    membership_type=individual,
                    action=MembershipLog.Action.terminated,
                ),
                username=username,
                membership_type=individual,
                reason_category=MembershipTerminationFeedback.ReasonCategory.other,
                reason_text=reason_text,
                reason_text_cleared_at=cleared_at,
            )

        self.assertEqual(self._sidebar_badge("admin:core_membershipterminationfeedback_changelist"), "1")
