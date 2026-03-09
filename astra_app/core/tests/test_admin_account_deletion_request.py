import datetime
from unittest.mock import call, patch

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from python_freeipa import exceptions

from core.account_deletion import (
    execute_account_deletion_request,
    get_account_deletion_blockers,
    invalidate_sessions_for_freeipa_username,
)
from core.freeipa.user import FreeIPAUser
from core.models import AccountDeletionRequest, Election, Organization, VotingCredential
from core.tests.utils_test_data import ensure_core_categories


class AdminAccountDeletionRequestTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _admin_user(self, username: str = "alice") -> FreeIPAUser:
        return FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})

    def _non_admin_user(self, username: str = "alice") -> FreeIPAUser:
        return FreeIPAUser(username, {"uid": [username], "memberof_group": []})

    def _create_request(
        self,
        *,
        username: str = "alice",
        status: str = AccountDeletionRequest.Status.pending_review,
        reason_text: str = "Please remove this account after review.",
        manual_review_required: bool = True,
        blocker_codes: list[str] | None = None,
    ) -> AccountDeletionRequest:
        return AccountDeletionRequest.objects.create(
            username=username,
            request_source=AccountDeletionRequest.RequestSource.user_settings,
            status=status,
            reason_category=AccountDeletionRequest.ReasonCategory.privacy,
            reason_text=reason_text,
            manual_review_required=manual_review_required,
            blocker_codes=list(blocker_codes or ["organization_representative", "open_election"]),
            fresh_auth_at=timezone.now() - datetime.timedelta(minutes=5),
        )

    def _create_session_for_username(self, username: str) -> str:
        session = SessionStore()
        session["_freeipa_username"] = username
        session.save()
        return str(session.session_key)

    def test_get_account_deletion_blockers_reports_live_org_and_election_blockers(self) -> None:
        Organization.objects.create(name="Example Org", representative="alice")
        election = Election.objects.create(
            name="Board 2026",
            description="",
            start_datetime=timezone.now() - datetime.timedelta(days=1),
            end_datetime=timezone.now() + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=10,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            freeipa_username="alice",
            public_id="board-2026-alice",
            weight=1,
        )

        blocker_codes, warnings = get_account_deletion_blockers("alice")

        self.assertEqual(set(blocker_codes), {"organization_representative", "open_election"})
        self.assertEqual(len(warnings), 2)

    def test_invalidate_sessions_for_freeipa_username_deletes_only_matching_sessions(self) -> None:
        alice_one = self._create_session_for_username("alice")
        alice_two = self._create_session_for_username("alice")
        bob_session = self._create_session_for_username("bob")

        invalidated = invalidate_sessions_for_freeipa_username("alice")

        self.assertEqual(invalidated, 2)
        self.assertFalse(Session.objects.filter(session_key=alice_one).exists())
        self.assertFalse(Session.objects.filter(session_key=alice_two).exists())
        self.assertTrue(Session.objects.filter(session_key=bob_session).exists())

    def test_execute_account_deletion_request_rechecks_live_blockers_before_delete(self) -> None:
        deletion_request = self._create_request(
            username="alice",
            manual_review_required=False,
            blocker_codes=[],
        )
        Organization.objects.create(name="Example Org", representative="alice")

        with patch("core.account_deletion.FreeIPAUser.delete", autospec=True) as delete_mock:
            with self.assertRaisesRegex(RuntimeError, "organization representative"):
                execute_account_deletion_request(deletion_request)

        delete_mock.assert_not_called()

    def test_execute_account_deletion_request_treats_missing_freeipa_user_as_success(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        target_session = self._create_session_for_username("bob")

        with patch("core.account_deletion.FreeIPAUser.delete", autospec=True, side_effect=exceptions.NotFound):
            invalidated = execute_account_deletion_request(deletion_request)

        self.assertEqual(invalidated, 1)
        self.assertFalse(Session.objects.filter(session_key=target_session).exists())

    def test_admin_changelist_requires_admin_session(self) -> None:
        response = self.client.get(reverse("admin:core_accountdeletionrequest_changelist"), follow=False)

        self.assertEqual(response.status_code, 302)

    def test_admin_changelist_denies_authenticated_non_admin_user(self) -> None:
        self._login_as_freeipa_admin("alice")
        non_admin_user = self._non_admin_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=non_admin_user):
            response = self.client.get(reverse("admin:core_accountdeletionrequest_changelist"), follow=False)

        self.assertEqual(response.status_code, 302)

    def test_admin_changelist_renders_review_columns_and_actions(self) -> None:
        self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.get(reverse("admin:core_accountdeletionrequest_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alice")
        self.assertContains(response, "Pending review")
        self.assertContains(response, "User settings")
        self.assertContains(response, "Yes")
        self.assertContains(response, "organization_representative, open_election")
        self.assertContains(response, "Approve request(s)")
        self.assertContains(response, "Mark as pending privilege check")
        self.assertContains(response, "Reject request(s)")
        self.assertContains(response, "Cancel request(s)")

    def test_admin_action_approve_executes_deletion_and_completes_request(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        target_session = self._create_session_for_username("bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None) as delete_mock,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.completed)
        self.assertIsNotNone(deletion_request.reason_cleanup_due_at)
        self.assertFalse(Session.objects.filter(session_key=target_session).exists())
        delete_mock.assert_called_once()
        self.assertContains(response, "Approved and executed 1 request(s).")

        change_messages = list(
            LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE)
            .order_by("action_time")
            .values_list("change_message", flat=True)
        )
        self.assertEqual(len(change_messages), 2)
        self.assertTrue(any("Pending review -> Approved" in message for message in change_messages))
        self.assertTrue(any("Approved -> Completed" in message for message in change_messages))

    def test_admin_action_approve_emits_approved_and_completed_signals(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None),
            patch("core.admin.schedule_account_deletion_signal", autospec=True) as schedule_signal,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            schedule_signal.call_args_list,
            [
                call(
                    event_key="account_deletion_approved",
                    account_deletion_request_id=deletion_request.pk,
                    actor="admin",
                ),
                call(
                    event_key="account_deletion_completed",
                    account_deletion_request_id=deletion_request.pk,
                    actor="admin",
                ),
            ],
        )

    def test_admin_action_approve_leaves_request_approved_when_execution_fails(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch(
                "core.account_deletion.FreeIPAUser.delete",
                autospec=True,
                side_effect=RuntimeError("delete failed"),
            ),
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.approved)
        self.assertIsNone(deletion_request.reason_cleanup_due_at)
        self.assertContains(response, "delete failed")

        change_messages = list(
            LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE)
            .order_by("action_time")
            .values_list("change_message", flat=True)
        )
        self.assertTrue(any("Pending review -> Approved" in message for message in change_messages))
        self.assertTrue(any("Deletion execution failed" in message for message in change_messages))
        self.assertFalse(any("Approved -> Completed" in message for message in change_messages))

    def test_admin_action_approve_invalidates_sessions_before_delete_failure(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        target_session = self._create_session_for_username("bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch(
                "core.account_deletion.FreeIPAUser.delete",
                autospec=True,
                side_effect=RuntimeError("delete failed"),
            ),
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.approved)
        self.assertFalse(Session.objects.filter(session_key=target_session).exists())
        self.assertContains(response, "delete failed")

    def test_admin_action_approve_keeps_request_approved_when_completion_save_fails(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        target_session = self._create_session_for_username("bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")
        original_save = AccountDeletionRequest.save
        completion_save_attempted = False

        def save_side_effect(instance: AccountDeletionRequest, *args, **kwargs) -> None:
            nonlocal completion_save_attempted
            if (
                instance.pk == deletion_request.pk
                and instance.status == AccountDeletionRequest.Status.completed
                and not completion_save_attempted
            ):
                completion_save_attempted = True
                raise RuntimeError("completion save failed")

            original_save(instance, *args, **kwargs)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None) as delete_mock,
            patch.object(AccountDeletionRequest, "save", autospec=True, side_effect=save_side_effect),
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.approved)
        self.assertIsNone(deletion_request.reason_cleanup_due_at)
        self.assertFalse(Session.objects.filter(session_key=target_session).exists())
        delete_mock.assert_called_once()
        self.assertContains(response, "completion save failed")

        change_messages = list(
            LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE)
            .order_by("action_time")
            .values_list("change_message", flat=True)
        )
        self.assertTrue(any("Pending review -> Approved" in message for message in change_messages))
        self.assertTrue(any("Deletion execution failed after FreeIPA delete" in message for message in change_messages))
        self.assertFalse(any("Approved -> Completed" in message for message in change_messages))

    def test_admin_action_needs_privilege_check_sets_status_without_scheduling_cleanup(self) -> None:
        deletion_request = self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "mark_requests_pending_privilege_check",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.pending_privilege_check)
        self.assertIsNone(deletion_request.reason_cleanup_due_at)

        log_entry = LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE).latest("action_time")
        self.assertEqual(
            log_entry.change_message,
            "[{\"changed\": {\"fields\": [\"status\"], \"name\": \"Pending review -> Pending privilege check\"}}]",
        )

    def test_admin_action_needs_privilege_check_emits_signal(self) -> None:
        deletion_request = self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.schedule_account_deletion_signal", autospec=True) as schedule_signal,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "mark_requests_pending_privilege_check",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        schedule_signal.assert_called_once_with(
            event_key="account_deletion_pending_privilege_check",
            account_deletion_request_id=deletion_request.pk,
            actor="alice",
        )

    def test_admin_action_reject_sets_closed_status_and_schedules_reason_cleanup(self) -> None:
        deletion_request = self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "reject_requests",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.rejected)
        self.assertIsNotNone(deletion_request.reason_cleanup_due_at)

    def test_admin_action_reject_emits_signal(self) -> None:
        deletion_request = self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.schedule_account_deletion_signal", autospec=True) as schedule_signal,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "reject_requests",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        schedule_signal.assert_called_once_with(
            event_key="account_deletion_rejected",
            account_deletion_request_id=deletion_request.pk,
            actor="alice",
        )

    def test_admin_action_cancel_sets_closed_status_and_schedules_reason_cleanup(self) -> None:
        deletion_request = self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "cancel_requests",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.cancelled)
        self.assertIsNotNone(deletion_request.reason_cleanup_due_at)

        log_entry = LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE).latest("action_time")
        self.assertEqual(log_entry.change_message, "[{\"changed\": {\"fields\": [\"status\"], \"name\": \"Pending review -> Cancelled\"}}]")

    def test_admin_action_cancel_emits_signal(self) -> None:
        deletion_request = self._create_request()
        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.schedule_account_deletion_signal", autospec=True) as schedule_signal,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "cancel_requests",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        schedule_signal.assert_called_once_with(
            event_key="account_deletion_cancelled",
            account_deletion_request_id=deletion_request.pk,
            actor="alice",
        )

    def test_admin_action_skips_closed_request_reactivation_and_warns_operator(self) -> None:
        active_request = self._create_request(
            username="alice-active",
            manual_review_required=False,
            blocker_codes=[],
        )
        closed_request = self._create_request(
            username="alice-closed",
            status=AccountDeletionRequest.Status.cancelled,
        )
        closed_request.refresh_from_db()
        self.assertIsNotNone(closed_request.reason_cleanup_due_at)

        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None),
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(active_request.pk), str(closed_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        active_request.refresh_from_db()
        closed_request.refresh_from_db()
        self.assertEqual(active_request.status, AccountDeletionRequest.Status.completed)
        self.assertIsNotNone(active_request.reason_cleanup_due_at)
        self.assertEqual(closed_request.status, AccountDeletionRequest.Status.cancelled)
        self.assertIsNotNone(closed_request.reason_cleanup_due_at)
        self.assertContains(response, "Approved and executed 1 request(s).")
        self.assertContains(response, "Skipped 1 request(s) that cannot transition to Approved.")

        self.assertTrue(LogEntry.objects.filter(object_id=str(active_request.pk), action_flag=CHANGE).exists())
        self.assertFalse(
            LogEntry.objects.filter(object_id=str(closed_request.pk), action_flag=CHANGE).exists()
        )

    def test_admin_action_does_not_reactivate_completed_request(self) -> None:
        completed_request = self._create_request(
            username="alice-complete",
            status=AccountDeletionRequest.Status.completed,
        )
        completed_request.refresh_from_db()
        self.assertIsNotNone(completed_request.reason_cleanup_due_at)

        self._login_as_freeipa_admin("alice")
        admin_user = self._admin_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "mark_requests_pending_privilege_check",
                    "_selected_action": [str(completed_request.pk)],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        completed_request.refresh_from_db()
        self.assertEqual(completed_request.status, AccountDeletionRequest.Status.completed)
        self.assertContains(response, "Skipped 1 request(s) that cannot transition to Pending privilege check.")
        self.assertFalse(
            LogEntry.objects.filter(object_id=str(completed_request.pk), action_flag=CHANGE).exists()
        )

    def test_admin_action_approve_shows_confirmation_before_execution(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True) as delete_mock,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.pending_review)
        delete_mock.assert_not_called()
        self.assertContains(response, "Approve and Execute Account Deletion Requests")
        self.assertContains(
            response,
            "This action will immediately attempt to delete the selected FreeIPA account",
        )

    def test_admin_action_approve_confirmation_clears_select_across_and_confirms_only_visible_ids(self) -> None:
        selected_request = self._create_request(
            username="selected-user",
            status=AccountDeletionRequest.Status.pending_review,
            manual_review_required=False,
            blocker_codes=[],
        )
        widened_request = self._create_request(
            username="widened-user",
            status=AccountDeletionRequest.Status.pending_privilege_check,
            manual_review_required=False,
            blocker_codes=[],
        )
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")
        changelist_url = reverse("admin:core_accountdeletionrequest_changelist")
        filtered_url = f"{changelist_url}?status__exact={AccountDeletionRequest.Status.pending_review}"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None) as delete_mock,
        ):
            confirmation_response = self.client.post(
                filtered_url,
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(selected_request.pk)],
                    "select_across": "1",
                },
                follow=True,
            )
            execution_response = self.client.post(
                changelist_url,
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(selected_request.pk)],
                    "post": "yes",
                    "select_across": "0",
                },
                follow=True,
            )

        self.assertEqual(confirmation_response.status_code, 200)
        self.assertContains(confirmation_response, "selected-user")
        self.assertNotContains(confirmation_response, "widened-user")
        self.assertContains(confirmation_response, 'name="select_across" value="0"')

        self.assertEqual(execution_response.status_code, 200)
        selected_request.refresh_from_db()
        widened_request.refresh_from_db()
        self.assertEqual(selected_request.status, AccountDeletionRequest.Status.completed)
        self.assertEqual(widened_request.status, AccountDeletionRequest.Status.pending_privilege_check)
        delete_mock.assert_called_once()
        self.assertContains(execution_response, "Approved and executed 1 request(s).")

    def test_admin_action_approve_runtime_blocker_failure_persists_fresh_blocker_metadata(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        Organization.objects.create(name="Example Org", representative="bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True) as delete_mock,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.approved)
        self.assertTrue(deletion_request.manual_review_required)
        self.assertEqual(deletion_request.blocker_codes, ["organization_representative"])
        delete_mock.assert_not_called()
        self.assertContains(response, "Manual review required because you are an organization representative.")

    def test_admin_action_approve_confirmed_execution_refetches_rows_with_select_for_update(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None),
            patch.object(
                AccountDeletionRequest.objects,
                "select_for_update",
                wraps=AccountDeletionRequest.objects.select_for_update,
            ) as select_for_update_mock,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.completed)
        self.assertGreaterEqual(select_for_update_mock.call_count, 1)

    def test_admin_action_approve_rechecks_live_blockers_during_confirmed_execution(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        Organization.objects.create(name="Example Org", representative="bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True) as delete_mock,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.approved)
        delete_mock.assert_not_called()
        self.assertContains(response, "Manual review required because you are an organization representative.")

        change_messages = list(
            LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE)
            .order_by("action_time")
            .values_list("change_message", flat=True)
        )
        self.assertTrue(any("Pending review -> Approved" in message for message in change_messages))
        self.assertTrue(any("Deletion execution failed" in message for message in change_messages))

    def test_admin_action_approve_completes_when_freeipa_user_is_already_missing(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        target_session = self._create_session_for_username("bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, side_effect=exceptions.NotFound),
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.completed)
        self.assertIsNotNone(deletion_request.reason_cleanup_due_at)
        self.assertFalse(Session.objects.filter(session_key=target_session).exists())
        self.assertContains(response, "Approved and executed 1 request(s).")

    def test_admin_action_approve_retries_already_approved_request(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            status=AccountDeletionRequest.Status.approved,
            manual_review_required=False,
            blocker_codes=[],
        )
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None) as delete_mock,
        ):
            response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.completed)
        delete_mock.assert_called_once()
        self.assertContains(response, "Approved and executed 1 request(s).")

        change_messages = list(
            LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE)
            .order_by("action_time")
            .values_list("change_message", flat=True)
        )
        self.assertEqual(len(change_messages), 1)
        self.assertTrue(any("Approved -> Completed" in message for message in change_messages))

    def test_admin_action_approve_retry_recovers_after_completion_save_failure(self) -> None:
        deletion_request = self._create_request(
            username="bob",
            manual_review_required=False,
            blocker_codes=[],
        )
        target_session = self._create_session_for_username("bob")
        self._login_as_freeipa_admin("admin")
        admin_user = self._admin_user("admin")
        original_save = AccountDeletionRequest.save
        completion_save_attempted = False

        def save_side_effect(instance: AccountDeletionRequest, *args, **kwargs) -> None:
            nonlocal completion_save_attempted
            if (
                instance.pk == deletion_request.pk
                and instance.status == AccountDeletionRequest.Status.completed
                and not completion_save_attempted
            ):
                completion_save_attempted = True
                raise RuntimeError("completion save failed")

            original_save(instance, *args, **kwargs)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, return_value=None) as delete_mock,
            patch.object(AccountDeletionRequest, "save", autospec=True, side_effect=save_side_effect),
        ):
            first_response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(first_response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.approved)
        self.assertFalse(Session.objects.filter(session_key=target_session).exists())
        delete_mock.assert_called_once()
        self.assertContains(first_response, "completion save failed")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.account_deletion.FreeIPAUser.delete", autospec=True, side_effect=exceptions.NotFound) as retry_delete_mock,
        ):
            second_response = self.client.post(
                reverse("admin:core_accountdeletionrequest_changelist"),
                data={
                    "action": "approve_requests",
                    "_selected_action": [str(deletion_request.pk)],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(second_response.status_code, 200)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.completed)
        self.assertIsNotNone(deletion_request.reason_cleanup_due_at)
        retry_delete_mock.assert_called_once()
        self.assertContains(second_response, "Approved and executed 1 request(s).")

        change_messages = list(
            LogEntry.objects.filter(object_id=str(deletion_request.pk), action_flag=CHANGE)
            .order_by("action_time")
            .values_list("change_message", flat=True)
        )
        self.assertTrue(any("Pending review -> Approved" in message for message in change_messages))
        self.assertTrue(any("Deletion execution failed after FreeIPA delete" in message for message in change_messages))
        self.assertTrue(any("Approved -> Completed" in message for message in change_messages))
