import datetime
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.middleware.csrf import get_token
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core import views_settings
from core.models import Election, Membership, MembershipLog, MembershipType, Organization, VotingCredential
from core.tests.utils_test_data import ensure_core_categories


class SelfServiceMembershipPrivacyTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        cache.clear()
        self.factory = RequestFactory()
        self._agreements_enabled_patcher = patch(
            "core.views_settings.has_enabled_agreements",
            autospec=True,
            return_value=False,
        )
        self._agreements_enabled_patcher.start()
        self.addCleanup(self._agreements_enabled_patcher.stop)

    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _add_csrf(self, request) -> str:
        token = get_token(request)
        request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
        if request.method == "POST":
            request.POST = request.POST.copy()
            request.POST["csrfmiddlewaretoken"] = token
        return token

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            email=f"{username}@example.org",
            groups_list=[],
            has_perm=lambda _perm: False,
            is_staff=False,
        )

    def _fake_freeipa_user(self, username: str = "alice") -> SimpleNamespace:
        return SimpleNamespace(
            username=username,
            first_name="Alice",
            last_name="User",
            full_name="Alice User",
            email=f"{username}@example.org",
            is_authenticated=True,
            groups_list=["almalinux-individual"],
            fas_is_private=False,
            remove_from_group=lambda *, group_name: None,
            _user_data={
                "uid": [username],
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "mail": [f"{username}@example.org"],
                "fasstatusnote": ["US"],
                "fasIsPrivate": [False],
            },
        )

    def _settings_initial_payload(self, response) -> dict[str, object]:
        html = response.content.decode("utf-8")
        match = re.search(
            r'<script id="settings-initial-payload" type="application/json">(?P<payload>.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return json.loads(match.group("payload"))

    def _create_membership_type(self, *, code: str, name: str, group_cn: str, category_id: str = "individual") -> None:
        MembershipType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "group_cn": group_cn,
                "category_id": category_id,
                "sort_order": 0,
                "enabled": True,
            },
        )

    def test_new_forms_exist_for_privacy_termination_and_deletion(self) -> None:
        from core.forms_selfservice import (  # noqa: PLC0415
            AccountDeletionRequestForm,
            MembershipTerminationForm,
            PrivacySettingsForm,
            ProfileForm,
        )

        profile_form = ProfileForm()
        self.assertNotIn("fasIsPrivate", profile_form.fields)

        privacy_form = PrivacySettingsForm(initial={"fasIsPrivate": True})
        self.assertIn("fasIsPrivate", privacy_form.fields)
        self.assertNotIn("givenname", privacy_form.fields)

        termination_form = MembershipTerminationForm()
        self.assertIn("reason_category", termination_form.fields)
        self.assertNotIn("confirm_membership_name", termination_form.fields)
        self.assertIn("current_password", termination_form.fields)

        deletion_form = AccountDeletionRequestForm()
        self.assertIn("acknowledge_retained_data", deletion_form.fields)
        self.assertNotIn("confirm_account_name", deletion_form.fields)
        self.assertIn("current_password", deletion_form.fields)

    def test_settings_shell_renders_membership_and_privacy_tabs(self) -> None:
        request = self.factory.get("/settings/?tab=privacy")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_freeipa_user()):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        payload = self._settings_initial_payload(response)
        self.assertIn('data-settings-root=""', html)
        self.assertEqual(payload["active_tab"], "privacy")
        self.assertIn("membership", payload["tabs"])
        self.assertIn("privacy", payload["tabs"])
        self.assertIn("account_deletion_form", payload["privacy"])
        self.assertIn("active_memberships", payload["membership"])

    def test_profile_tab_no_longer_renders_private_profile_toggle(self) -> None:
        request = self.factory.get("/settings/?tab=profile")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_freeipa_user()):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertNotIn("Private profile", html)

    def test_membership_termination_post_requires_current_password(self) -> None:
        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        request = self.factory.post(
            reverse("settings-membership-terminate", kwargs={"membership_type_code": "individual"}),
            data={
                "reason_category": "privacy",
                "reason_text": "Leaving the community for now.",
            },
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        with (
            patch("core.views_settings._get_freeipa_client", autospec=True),
            patch("core.views_settings.transaction.on_commit", autospec=True, side_effect=lambda callback: callback()),
        ):
            response = views_settings.settings_membership_terminate(request, membership_type_code="individual")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=membership")
        self.assertFalse(
            MembershipLog.objects.filter(
                actor_username="alice",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.terminated,
            ).exists()
        )
        msgs = [message.message for message in get_messages(request)]
        self.assertIn("Unable to terminate this membership.", msgs)

    def test_membership_termination_post_creates_feedback_and_log(self) -> None:
        from core.models import MembershipTerminationFeedback  # noqa: PLC0415

        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        ipa_user = self._fake_freeipa_user()
        ipa_user.groups_list = ["almalinux-individual"]

        request = self.factory.post(
            reverse("settings-membership-terminate", kwargs={"membership_type_code": "individual"}),
            data={
                "reason_category": "privacy",
                "reason_text": "Please remove all committee-only contact paths.",
                "current_password": "correct horse battery staple",
            },
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        with (
            patch("core.views_settings._get_freeipa_client", autospec=True),
            patch("core.membership.FreeIPAUser.get", autospec=True, return_value=ipa_user),
            patch.object(ipa_user, "remove_from_group", autospec=True),
            patch("core.views_settings.transaction.on_commit", autospec=True, side_effect=lambda callback: callback()),
            patch("core.views_settings.membership_self_terminated.send", autospec=True) as signal_send,
        ):
            response = views_settings.settings_membership_terminate(request, membership_type_code="individual")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=membership&status=terminated")
        log = MembershipLog.objects.get(
            actor_username="alice",
            target_username="alice",
            membership_type_id="individual",
            action=MembershipLog.Action.terminated,
        )
        feedback = MembershipTerminationFeedback.objects.get(membership_log=log)
        self.assertEqual(feedback.reason_category, MembershipTerminationFeedback.ReasonCategory.privacy)
        self.assertEqual(feedback.reason_text, "Please remove all committee-only contact paths.")
        self.assertIsNotNone(feedback.reason_cleanup_due_at)
        retention_window = feedback.reason_cleanup_due_at - feedback.created_at
        self.assertGreaterEqual(retention_window, datetime.timedelta(days=29, hours=23, minutes=59))
        self.assertLessEqual(retention_window, datetime.timedelta(days=30, minutes=1))
        self.assertFalse(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())
        signal_send.assert_called_once()

    def test_membership_termination_reauth_failure_logs_exception_object(self) -> None:
        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        request = self.factory.post(
            reverse("settings-membership-terminate", kwargs={"membership_type_code": "individual"}),
            data={
                "reason_category": "privacy",
                "reason_text": "Need to leave.",
                "current_password": "incorrect",
            },
            REMOTE_ADDR="198.51.100.20",
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")

        reauth_error = RuntimeError("reauth down")
        with (
            patch("core.views_settings._reauthenticate_destructive_action", autospec=True, side_effect=reauth_error),
            patch("core.views_settings.logger.info") as info_mock,
        ):
            response = views_settings.settings_membership_terminate(request, membership_type_code="individual")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=membership")
        info_mock.assert_called_once_with(
            "Membership self-termination reauthentication failed username=%s error=%s",
            "alice",
            reauth_error,
        )
        messages_list = [message.message for message in get_messages(request)]
        self.assertIn("Unable to verify your current password.", messages_list)

    def test_membership_termination_accepts_active_membership_without_expiry(self) -> None:
        from core.models import MembershipTerminationFeedback  # noqa: PLC0415

        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=None,
        )

        request = self.factory.post(
            reverse("settings-membership-terminate", kwargs={"membership_type_code": "individual"}),
            data={
                "reason_category": "privacy",
                "reason_text": "No end date should still count as active.",
                "current_password": "correct horse battery staple",
            },
            REMOTE_ADDR="198.51.100.10",
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")

        with (
            patch("core.views_settings._reauthenticate_destructive_action", autospec=True, return_value=True),
            patch("core.views_settings.remove_user_from_group", autospec=True, return_value=True),
            patch("core.views_settings.transaction.on_commit", autospec=True, side_effect=lambda callback: callback()),
            patch("core.views_settings.membership_self_terminated.send", autospec=True) as signal_send,
        ):
            response = views_settings.settings_membership_terminate(request, membership_type_code="individual")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=membership&status=terminated")
        feedback = MembershipTerminationFeedback.objects.get(username="alice", membership_type_id="individual")
        self.assertEqual(feedback.reason_text, "No end date should still count as active.")
        self.assertFalse(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())
        signal_send.assert_called_once()

    def test_membership_termination_reports_partial_failure_after_ipa_removal(self) -> None:
        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        request = self.factory.post(
            reverse("settings-membership-terminate", kwargs={"membership_type_code": "individual"}),
            data={
                "reason_category": "privacy",
                "reason_text": "Please log and alert on DB failure.",
                "current_password": "correct horse battery staple",
            },
            REMOTE_ADDR="198.51.100.11",
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")

        with (
            patch("core.views_settings._reauthenticate_destructive_action", autospec=True, return_value=True),
            patch("core.views_settings.remove_user_from_group", autospec=True, return_value=True),
            patch(
                "core.views_settings.MembershipLog.create_for_termination",
                autospec=True,
                side_effect=RuntimeError("database write failed"),
            ),
            patch("core.views_settings.logger.critical", autospec=True) as critical_log,
        ):
            response = views_settings.settings_membership_terminate(request, membership_type_code="individual")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=membership")
        self.assertTrue(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())
        self.assertTrue(critical_log.called)
        messages_list = [message.message for message in get_messages(request)]
        self.assertTrue(
            any(
                "We removed your membership in FreeIPA, but failed to finish recording the change locally."
                in message
                for message in messages_list
            )
        )

    def test_privacy_deletion_request_submission_creates_single_active_request(self) -> None:
        from core.models import AccountDeletionRequest  # noqa: PLC0415

        request = self.factory.post(
            reverse("settings-account-deletion-request"),
            data={
                "reason_category": "privacy",
                "reason_text": "Please delete my account once review is complete.",
                "acknowledge_retained_data": "on",
                "current_password": "correct horse battery staple",
            },
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        with (
            patch("core.views_settings._get_freeipa_client", autospec=True),
            patch("core.views_settings.transaction.on_commit", autospec=True, side_effect=lambda callback: callback()),
            patch("core.views_settings.account_deletion_requested.send", autospec=True) as signal_send,
        ):
            first_response = views_settings.settings_account_deletion_request(request)
            second_response = views_settings.settings_account_deletion_request(request)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(AccountDeletionRequest.objects.filter(username="alice").count(), 1)
        deletion_request = AccountDeletionRequest.objects.get(username="alice")
        self.assertEqual(deletion_request.status, AccountDeletionRequest.Status.pending_review)
        self.assertEqual(deletion_request.reason_category, AccountDeletionRequest.ReasonCategory.privacy)
        self.assertEqual(
            deletion_request.reason_text,
            "Please delete my account once review is complete.",
        )
        self.assertIsNone(deletion_request.reason_cleanup_due_at)
        signal_send.assert_called_once()

    def test_account_deletion_request_existing_active_request_short_circuits_before_reauth(self) -> None:
        from core.models import AccountDeletionRequest  # noqa: PLC0415

        AccountDeletionRequest.objects.create(
            username="alice",
            status=AccountDeletionRequest.Status.pending_review,
            reason_category=AccountDeletionRequest.ReasonCategory.privacy,
            reason_text="Already pending.",
        )

        request = self.factory.post(
            reverse("settings-account-deletion-request"),
            data={
                "reason_category": "privacy",
                "reason_text": "Please do not burn a new token.",
                "acknowledge_retained_data": "on",
                "current_password": "correct horse battery staple",
            },
            REMOTE_ADDR="198.51.100.12",
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")

        with (
            patch("core.views_settings._allow_destructive_action", autospec=True, return_value=True) as allow_action,
            patch("core.views_settings._reauthenticate_destructive_action", autospec=True, return_value=True) as reauth,
        ):
            response = views_settings.settings_account_deletion_request(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=privacy&status=deletion-requested")
        self.assertEqual(AccountDeletionRequest.objects.filter(username="alice").count(), 1)
        allow_action.assert_not_called()
        reauth.assert_not_called()
        messages_list = [message.message for message in get_messages(request)]
        self.assertIn("Your existing account deletion request is still pending review.", messages_list)

    def test_account_deletion_request_reauth_failure_logs_exception_object(self) -> None:
        request = self.factory.post(
            reverse("settings-account-deletion-request"),
            data={
                "reason_category": "privacy",
                "reason_text": "Please delete my account.",
                "acknowledge_retained_data": "on",
                "current_password": "incorrect",
            },
            REMOTE_ADDR="198.51.100.21",
        )
        self._add_session_and_messages(request)
        self._add_csrf(request)
        request.user = self._auth_user("alice")

        reauth_error = RuntimeError("reauth down")
        with (
            patch("core.views_settings._allow_destructive_action", autospec=True, return_value=True),
            patch("core.views_settings._reauthenticate_destructive_action", autospec=True, side_effect=reauth_error),
            patch("core.views_settings.logger.info") as info_mock,
        ):
            response = views_settings.settings_account_deletion_request(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=privacy")
        info_mock.assert_called_once_with(
            "Account deletion request reauthentication failed username=%s error=%s",
            "alice",
            reauth_error,
        )
        messages_list = [message.message for message in get_messages(request)]
        self.assertIn("Unable to verify your current password.", messages_list)

    def test_account_deletion_request_schedules_reason_cleanup_when_closed(self) -> None:
        from core.models import AccountDeletionRequest  # noqa: PLC0415

        deletion_request = AccountDeletionRequest.objects.create(
            username="alice",
            status=AccountDeletionRequest.Status.pending_review,
            reason_category=AccountDeletionRequest.ReasonCategory.privacy,
            reason_text="Please remove this after the request is closed.",
        )

        deletion_request.status = AccountDeletionRequest.Status.cancelled
        deletion_request.save()

        deletion_request.refresh_from_db()
        self.assertIsNotNone(deletion_request.reason_cleanup_due_at)
        retention_window = deletion_request.reason_cleanup_due_at - deletion_request.updated_at
        self.assertGreaterEqual(retention_window, datetime.timedelta(days=29, hours=23, minutes=59))
        self.assertLessEqual(retention_window, datetime.timedelta(days=30, minutes=1))

    def test_privacy_tab_shows_manual_review_warnings_for_representatives_and_elections(self) -> None:
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
            public_id="public-id",
            weight=1,
        )

        request = self.factory.get("/settings/?tab=privacy")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_freeipa_user()):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("manual review", html.lower())
        self.assertIn("organization representative", html.lower())
        self.assertIn("election", html.lower())

    def test_privacy_tab_hides_live_deletion_form_when_request_is_active(self) -> None:
        from core.models import AccountDeletionRequest  # noqa: PLC0415

        AccountDeletionRequest.objects.create(
            username="alice",
            status=AccountDeletionRequest.Status.pending_review,
            reason_category=AccountDeletionRequest.ReasonCategory.privacy,
            reason_text="Already pending.",
        )

        request = self.factory.get("/settings/?tab=privacy")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_freeipa_user()):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        payload = self._settings_initial_payload(response)
        self.assertEqual(payload["active_tab"], "privacy")
        self.assertIsNotNone(payload["privacy"]["active_deletion_request"])
        self.assertEqual(payload["privacy"]["active_deletion_request"]["status"], "pending_review")

    def test_privacy_toggle_save_does_not_require_country_code(self) -> None:
        request = self.factory.post(
            "/settings/",
            data={
                "tab": "privacy",
                "fasIsPrivate": "on",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")
        user_without_country = self._fake_freeipa_user()
        user_without_country._user_data.pop("fasstatusnote", None)

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=user_without_country),
            patch("core.views_settings._update_user_attrs", autospec=True, return_value=([], True)) as update_attrs,
        ):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=privacy&status=saved")
        update_attrs.assert_called_once()

    def test_client_ip_for_rate_limit_uses_remote_addr_only(self) -> None:
        request = self.factory.post(
            "/settings/privacy/delete-request/",
            REMOTE_ADDR="198.51.100.13",
            HTTP_X_FORWARDED_FOR="203.0.113.5, 198.51.100.13",
        )

        self.assertEqual(views_settings._client_ip_for_rate_limit(request), "198.51.100.13")

    def test_settings_shell_uses_password_only_confirmation_copy(self) -> None:
        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=60),
        )

        request = self.factory.get("/settings/?tab=privacy")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=self._fake_freeipa_user()):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        payload = self._settings_initial_payload(response)
        account_deletion_fields = payload["privacy"]["account_deletion_form"]["fields"]
        self.assertNotIn("confirm_account_name", {field["name"] for field in account_deletion_fields})
        membership_fields = payload["membership"]["active_memberships"][0]["termination_form"]["fields"]
        self.assertNotIn("confirm_membership_name", {field["name"] for field in membership_fields})
        self.assertIn("current_password", {field["name"] for field in account_deletion_fields})
        self.assertIn("current_password", {field["name"] for field in membership_fields})

    def test_selfservice_lifecycle_cleanup_clears_due_reason_text(self) -> None:
        from core.models import AccountDeletionRequest, MembershipTerminationFeedback  # noqa: PLC0415

        self._create_membership_type(
            code="individual",
            name="Individual",
            group_cn="almalinux-individual",
        )
        log = MembershipLog.objects.create(
            actor_username="alice",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.terminated,
            expires_at=timezone.now(),
        )
        feedback = MembershipTerminationFeedback.objects.create(
            membership_log=log,
            username="alice",
            membership_type_id="individual",
            reason_category=MembershipTerminationFeedback.ReasonCategory.privacy,
            reason_text="Remove the free text after the cleanup window.",
            reason_cleanup_due_at=timezone.now() - datetime.timedelta(hours=1),
        )
        deletion_request = AccountDeletionRequest.objects.create(
            username="alice",
            status=AccountDeletionRequest.Status.cancelled,
            reason_category=AccountDeletionRequest.ReasonCategory.privacy,
            reason_text="This free text should also be cleared.",
            reason_cleanup_due_at=timezone.now() - datetime.timedelta(hours=1),
        )

        with self.assertLogs("core.management.commands.selfservice_lifecycle_cleanup", level="INFO") as logs:
            call_command("selfservice_lifecycle_cleanup")

        feedback.refresh_from_db()
        deletion_request.refresh_from_db()
        self.assertEqual(feedback.reason_text, "")
        self.assertIsNotNone(feedback.reason_text_cleared_at)
        self.assertEqual(deletion_request.reason_text, "")
        self.assertIsNotNone(deletion_request.reason_text_cleared_at)
        self.assertTrue(
            any("Cleared" in line for line in logs.output),
            f"Expected a cleanup summary log, got: {logs.output}",
        )