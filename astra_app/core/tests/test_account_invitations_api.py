import datetime
import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import AccountInvitation, FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class AccountInvitationsApiTests(TestCase):
    """RED tests defining the invitation API contract for Vue/REST migration."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _make_freeipa_user(
        self,
        username: str,
        *,
        email: str | None = None,
        groups: list[str] | None = None,
    ) -> FreeIPAUser:
        user_data: dict[str, list[str]] = {
            "uid": [username],
            "memberof_group": list(groups or []),
        }
        if email is not None:
            user_data["mail"] = [email]
        return FreeIPAUser(username, user_data)

    def _committee_user(self) -> FreeIPAUser:
        return self._make_freeipa_user(
            "committee",
            email="committee@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

    def _datatables_query(self, *, length: int) -> dict[str, str]:
        """Build a valid DataTables query for invitation list endpoints."""
        return {
            "draw": "1",
            "start": "0",
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "order[0][name]": "invited_at",
            "columns[0][data]": "invitation_id",
            "columns[0][name]": "invited_at",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

    def _assert_authenticated_permission_denied(
        self,
        *,
        method: str,
        url: str,
        data: dict[str, object] | None = None,
        content_type: str | None = None,
    ) -> None:
        self._login_as_freeipa_user("unprivileged")
        unprivileged_user = self._make_freeipa_user(
            "unprivileged",
            email="unprivileged@example.com",
            groups=[],
        )

        client_method = getattr(self.client, method)
        request_kwargs: dict[str, object] = {"HTTP_ACCEPT": "application/json"}
        if data is not None:
            request_kwargs["data"] = data
        if content_type is not None:
            request_kwargs["content_type"] = content_type

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=unprivileged_user):
            response = client_method(url, **request_kwargs)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "Permission denied"})

    # === Pending Invitations List Endpoint ===

    def test_pending_invitations_endpoint_requires_authentication(self) -> None:
        """Anonymous users must be rejected with 403."""
        response = self.client.get(
            "/api/v1/membership/invitations/pending",
            data=self._datatables_query(length=50),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_pending_invitations_endpoint_requires_astra_add_membership_permission(self) -> None:
        """Users without ASTRA_ADD_MEMBERSHIP must be rejected with 403."""
        self._assert_authenticated_permission_denied(
            method="get",
            url="/api/v1/membership/invitations/pending",
            data=self._datatables_query(length=50),
        )

    def test_pending_invitations_detail_endpoint_requires_permission(self) -> None:
        self._assert_authenticated_permission_denied(
            method="get",
            url=reverse("api-account-invitations-pending-detail"),
            data=self._datatables_query(length=50),
        )

    def test_pending_invitations_endpoint_returns_datatables_envelope(self) -> None:
        """Pending invitations list must return DataTables-format JSON."""
        self._login_as_freeipa_user("committee")
        AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            note="Test invitation",
            invited_by_username="committee",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                "/api/v1/membership/invitations/pending",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("draw", payload)
        self.assertIn("recordsTotal", payload)
        self.assertIn("recordsFiltered", payload)
        self.assertIn("data", payload)
        self.assertEqual(payload["draw"], 1)
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(len(payload["data"]), 1)

    def test_pending_invitations_excludes_accepted_and_dismissed(self) -> None:
        """Pending endpoint must exclude accepted and dismissed invitations."""
        self._login_as_freeipa_user("committee")

        # Create pending, accepted, and dismissed invitations
        pending = AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )
        AccountInvitation.objects.create(
            email="accepted@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
        )
        AccountInvitation.objects.create(
            email="dismissed@example.com",
            invited_by_username="committee",
            dismissed_at=timezone.now(),
            dismissed_by_username="committee",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                "/api/v1/membership/invitations/pending",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(payload["data"][0]["invitation_id"], pending.pk)

    def test_pending_invitations_row_includes_required_fields(self) -> None:
        """Pending invitation rows must include email, full_name, note, status fields for parity."""
        self._login_as_freeipa_user("committee")
        organization = Organization.objects.create(
            name="Test Org",
            business_contact_email="contact@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Full Name",
            note="Test note",
            invited_by_username="committee",
            organization=organization,
            invited_at=timezone.now(),
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                "/api/v1/membership/invitations/pending",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["invitation_id"], invitation.pk)
        self.assertEqual(row["email"], "pending@example.com")
        self.assertEqual(row["full_name"], "Full Name")
        self.assertEqual(row["note"], "Test note")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["invited_by_username"], "committee")
        self.assertIsNotNone(row["invited_at"])
        if invitation.organization_id:
            self.assertEqual(row["organization_id"], organization.pk)
            self.assertEqual(row["organization_name"], "Test Org")

    def test_pending_invitations_detail_endpoint_returns_raw_datetime_contract(self) -> None:
        self._login_as_freeipa_user("committee")
        invited_at = timezone.now()
        last_sent_at = invited_at + datetime.timedelta(hours=2)
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Full Name",
            note="Test note",
            invited_by_username="committee",
            invited_at=invited_at,
            last_sent_at=last_sent_at,
        )
        invitation.refresh_from_db()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                reverse("api-account-invitations-pending-detail"),
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["invitation_id"], invitation.pk)
        self.assertEqual(row["invited_at"], invitation.invited_at.isoformat())
        self.assertEqual(row["last_sent_at"], invitation.last_sent_at.isoformat())

    def test_pending_invitations_detail_endpoint_scopes_records_total_before_search(self) -> None:
        self._login_as_freeipa_user("committee")
        AccountInvitation.objects.create(
            email="pending-match@example.com",
            invited_by_username="committee",
        )
        AccountInvitation.objects.create(
            email="pending-other@example.com",
            invited_by_username="committee",
        )
        AccountInvitation.objects.create(
            email="accepted@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
        )
        AccountInvitation.objects.create(
            email="dismissed@example.com",
            invited_by_username="committee",
            dismissed_at=timezone.now(),
            dismissed_by_username="committee",
        )

        query = self._datatables_query(length=50)
        query["search[value]"] = "pending-match"

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                reverse("api-account-invitations-pending-detail"),
                data=query,
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recordsTotal"], 2)
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["email"], "pending-match@example.com")

    # === Accepted Invitations List Endpoint ===

    def test_accepted_invitations_endpoint_requires_authentication(self) -> None:
        """Anonymous users must be rejected with 403."""
        response = self.client.get(
            "/api/v1/membership/invitations/accepted",
            data=self._datatables_query(length=50),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_accepted_invitations_endpoint_requires_permission(self) -> None:
        self._assert_authenticated_permission_denied(
            method="get",
            url="/api/v1/membership/invitations/accepted",
            data=self._datatables_query(length=50),
        )

    def test_accepted_invitations_endpoint_returns_datatables_envelope(self) -> None:
        """Accepted invitations list must return DataTables-format JSON."""
        self._login_as_freeipa_user("committee")
        AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
            freeipa_matched_usernames=["accepteduser"],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                "/api/v1/membership/invitations/accepted",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(len(payload["data"]), 1)

    def test_accepted_invitations_excludes_pending_and_dismissed(self) -> None:
        """Accepted endpoint must exclude pending and dismissed invitations."""
        self._login_as_freeipa_user("committee")

        AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )
        accepted = AccountInvitation.objects.create(
            email="accepted@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
        )
        AccountInvitation.objects.create(
            email="dismissed@example.com",
            invited_by_username="committee",
            dismissed_at=timezone.now(),
            dismissed_by_username="committee",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                "/api/v1/membership/invitations/accepted",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(payload["data"][0]["invitation_id"], accepted.pk)

    def test_accepted_invitations_row_includes_accepted_username(self) -> None:
        """Accepted invitation rows must include accepted_at and freeipa_matched_usernames."""
        self._login_as_freeipa_user("committee")
        accepted_at = timezone.now()
        invitation = AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            invited_by_username="committee",
            accepted_at=accepted_at,
            accepted_username="accepteduser",
            freeipa_matched_usernames=["accepteduser", "alternative"],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                "/api/v1/membership/invitations/accepted",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["invitation_id"], invitation.pk)
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["accepted_username"], "accepteduser")
        self.assertEqual(row["freeipa_matched_usernames"], ["accepteduser", "alternative"])
        self.assertIsNotNone(row["accepted_at"])

    def test_accepted_invitations_detail_endpoint_returns_raw_datetime_contract(self) -> None:
        self._login_as_freeipa_user("committee")
        accepted_at = timezone.now()
        invitation = AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            invited_by_username="committee",
            accepted_at=accepted_at,
            accepted_username="accepteduser",
            freeipa_matched_usernames=["accepteduser", "alternative"],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                reverse("api-account-invitations-accepted-detail"),
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["invitation_id"], invitation.pk)
        self.assertEqual(row["accepted_at"], accepted_at.isoformat())

    def test_accepted_invitations_detail_endpoint_requires_permission(self) -> None:
        self._assert_authenticated_permission_denied(
            method="get",
            url=reverse("api-account-invitations-accepted-detail"),
            data=self._datatables_query(length=50),
        )

    def test_accepted_invitations_detail_endpoint_scopes_records_total_before_search(self) -> None:
        self._login_as_freeipa_user("committee")
        AccountInvitation.objects.create(
            email="accepted-match@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepted-match",
        )
        AccountInvitation.objects.create(
            email="accepted-other@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepted-other",
        )
        AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )
        AccountInvitation.objects.create(
            email="dismissed@example.com",
            invited_by_username="committee",
            dismissed_at=timezone.now(),
            dismissed_by_username="committee",
        )

        query = self._datatables_query(length=50)
        query["search[value]"] = "accepted-match"

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.get(
                reverse("api-account-invitations-accepted-detail"),
                data=query,
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recordsTotal"], 2)
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["email"], "accepted-match@example.com")

    def test_account_invitations_page_uses_canonical_detail_endpoints(self) -> None:
        self._login_as_freeipa_user("committee")

        with (
            self.settings(FORCE_SCRIPT_NAME="/astra"),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
        ):
            response = self.client.get(reverse("account-invitations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-account-invitations-pending-api-url="{reverse("api-account-invitations-pending-detail")}"',
        )
        self.assertContains(
            response,
            f'data-account-invitations-accepted-api-url="{reverse("api-account-invitations-accepted-detail")}"',
        )
        self.assertContains(
            response,
            f'data-account-invitations-resend-api-url="{reverse("api-account-invitations-resend", args=[123456789])}"',
        )
        self.assertContains(
            response,
            f'data-account-invitations-dismiss-api-url="{reverse("api-account-invitations-dismiss", args=[123456789])}"',
        )

    # === Refresh Action Endpoint ===

    def test_refresh_endpoint_requires_authentication(self) -> None:
        """Anonymous users must be rejected with 403."""
        response = self.client.post(
            "/api/v1/membership/invitations/refresh",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_refresh_endpoint_requires_permission(self) -> None:
        """Users without ASTRA_ADD_MEMBERSHIP must be rejected."""
        self._assert_authenticated_permission_denied(
            method="post",
            url="/api/v1/membership/invitations/refresh",
        )

    def test_refresh_endpoint_returns_ok_json(self) -> None:
        """Refresh must return {ok: true, message: ...} on success."""
        self._login_as_freeipa_user("committee")
        AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.account_invitations.find_account_invitation_matches", return_value=[]),
        ):
            response = self.client.post(
                "/api/v1/membership/invitations/refresh",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("ok", payload)
        self.assertTrue(payload["ok"])
        self.assertIn("message", payload)

    # === Resend Action Endpoint ===

    def test_resend_endpoint_returns_ok_json_on_success(self) -> None:
        """Resend must return {ok: true, message: ...} on success."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            invited_by_username="committee",
            email_template_name="account-invite",
        )
        from post_office.models import EmailTemplate
        EmailTemplate.objects.update_or_create(
            name="account-invite",
            defaults={
                "subject": "Account invite",
                "content": "Hello {{ email }}",
                "html_content": "<p>Hello {{ email }}</p>",
            },
        )
        queued_email = SimpleNamespace(id=123)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.account_invitations.find_account_invitation_matches", return_value=[]),
            patch("core.account_invitations.queue_templated_email", return_value=queued_email),
        ):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("message", payload)

    def test_resend_endpoint_rate_limit_rejection_preserves_429_json_contract(self) -> None:
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            invited_by_username="committee",
            email_template_name="account-invite",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_invitations_api.find_account_invitation_matches", return_value=[]),
            patch("core.views_invitations_api.allow_request", return_value=False),
        ):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(),
            {"ok": False, "error": "Too many resend attempts. Try again shortly."},
        )

    def test_resend_endpoint_non_queued_send_failure_preserves_500_json_contract(self) -> None:
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            invited_by_username="committee",
            email_template_name="account-invite",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_invitations_api.find_account_invitation_matches", return_value=[]),
            patch("core.views_invitations_api.allow_request", return_value=True),
            patch("core.views_invitations_api._send_account_invitation_email", return_value="failed"),
        ):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"ok": False, "error": "Failed to resend invitation"},
        )

    def test_resend_endpoint_uses_invitation_email_template_name_directly(self) -> None:
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            invited_by_username="committee",
            email_template_name="account-invite-alt",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_invitations_api.find_account_invitation_matches", return_value=[]),
            patch("core.views_invitations_api.allow_request", return_value=True),
            patch("core.views_invitations_api._send_account_invitation_email", return_value="queued") as send_mock,
        ):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": f"Invitation resent to {invitation.email}"})
        send_mock.assert_called_once_with(
            invitation=invitation,
            actor_username="committee",
            template_name="account-invite-alt",
            now=send_mock.call_args.kwargs["now"],
        )

    def test_resend_endpoint_does_not_auto_accept_org_linked_invitation_from_email_match(self) -> None:
        """Org-linked invitation resend must preserve the canonical non-auto-accept rule."""
        self._login_as_freeipa_user("committee")
        organization = Organization.objects.create(
            name="Pending Claim Org",
            business_contact_email="contact@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="pending-claim@example.com",
            full_name="Pending Claim User",
            invited_by_username="committee",
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            organization=organization,
        )
        queued_email = SimpleNamespace(id=456)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_invitations_api.find_account_invitation_matches", return_value=["pendingclaim"]),
            patch("core.views_invitations_api.queue_templated_email", return_value=queued_email, create=True) as api_queue_mock,
            patch("core.account_invitations.queue_templated_email", return_value=queued_email) as shared_queue_mock,
        ):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": f"Invitation resent to {invitation.email}"})
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertEqual(invitation.freeipa_matched_usernames, ["pendingclaim"])
        self.assertEqual(invitation.send_count, 1)
        self.assertEqual(api_queue_mock.call_count + shared_queue_mock.call_count, 1)

    def test_resend_endpoint_email_match_emits_signal_only_for_first_transition(self) -> None:
        """API resend must use the canonical acceptance transition path exactly once."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            invited_by_username="committee",
            email_template_name="account-invite",
        )
        signal_module = importlib.import_module("core.signals")
        queued_email = SimpleNamespace(id=654)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch("core.views_invitations_api.find_account_invitation_matches", return_value=["pendinguser"]),
            patch("core.views_invitations_api.queue_templated_email", return_value=queued_email, create=True) as api_queue_mock,
            patch("core.account_invitations.queue_templated_email", return_value=queued_email) as shared_queue_mock,
            patch.object(signal_module.account_invitation_accepted, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            first_response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )
            second_response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(invitation.freeipa_matched_usernames, ["pendinguser"])
        api_queue_mock.assert_not_called()
        shared_queue_mock.assert_not_called()
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("actor"), "committee")
        self.assertEqual(kwargs.get("account_invitation").pk, invitation.pk)

    def test_resend_endpoint_returns_error_for_missing_invitation(self) -> None:
        """Resend must return {ok: false, error: ...} for missing invitation."""
        self._login_as_freeipa_user("committee")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.post(
                "/api/v1/membership/invitations/999999/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload.get("ok"))
        self.assertIn("error", payload)

    def test_resend_endpoint_requires_permission(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )

        self._assert_authenticated_permission_denied(
            method="post",
            url=reverse("api-account-invitations-resend", args=[invitation.pk]),
        )

    # === Dismiss Action Endpoint ===

    def test_dismiss_endpoint_returns_ok_json(self) -> None:
        """Dismiss must return {ok: true, message: ...} on success."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/dismiss",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("message", payload)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.dismissed_at)

    def test_dismiss_endpoint_requires_permission(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )

        self._assert_authenticated_permission_denied(
            method="post",
            url=reverse("api-account-invitations-dismiss", args=[invitation.pk]),
        )

    # === Bulk Action Endpoint ===

    def test_bulk_endpoint_returns_ok_json(self) -> None:
        """Bulk actions must return {ok: true, message: ...} on success."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.post(
                "/api/v1/membership/invitations/bulk",
                data={
                    "bulk_action": "dismiss",
                    "bulk_scope": "pending",
                    "selected": [str(invitation.pk)],
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("message", payload)

    def test_bulk_resend_preserves_current_no_acceptance_precheck_behavior(self) -> None:
        """Bulk resend must not introduce an email-match acceptance precheck in this slice."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            invited_by_username="committee",
            email_template_name="account-invite",
        )
        queued_email = SimpleNamespace(id=789)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()),
            patch(
                "core.views_invitations_api.find_account_invitation_matches",
                side_effect=AssertionError("bulk resend should not run acceptance precheck"),
            ),
            patch("core.views_invitations_api.queue_templated_email", return_value=queued_email, create=True) as api_queue_mock,
            patch("core.account_invitations.queue_templated_email", return_value=queued_email) as shared_queue_mock,
        ):
            response = self.client.post(
                "/api/v1/membership/invitations/bulk",
                data={
                    "bulk_action": "resend",
                    "bulk_scope": "pending",
                    "selected": [str(invitation.pk)],
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Resent 1 invitation(s)"})
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertEqual(invitation.send_count, 1)
        self.assertEqual(api_queue_mock.call_count + shared_queue_mock.call_count, 1)

    def test_bulk_endpoint_accepts_json_payload(self) -> None:
        """Bulk endpoint must accept JSON body payload used by Vue fetch."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
            email="pending-json@example.com",
            invited_by_username="committee",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._committee_user()):
            response = self.client.post(
                "/api/v1/membership/invitations/bulk",
                data=json.dumps(
                    {
                        "bulk_action": "dismiss",
                        "bulk_scope": "pending",
                        "selected": [invitation.pk],
                    }
                ),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("message", payload)

    def test_bulk_endpoint_requires_permission(self) -> None:
        """Bulk action must reject unauthorized users."""
        self._assert_authenticated_permission_denied(
            method="post",
            url="/api/v1/membership/invitations/bulk",
            data={
                "bulk_action": "dismiss",
                "selected": ["1"],
            },
        )
