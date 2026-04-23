import datetime
import json
from unittest.mock import patch
from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import AccountInvitation, AccountInvitationSend, FreeIPAPermissionGrant, Organization
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
        self._login_as_freeipa_user("unprivileged")
        unprivileged_user = self._make_freeipa_user(
            "unprivileged",
            email="unprivileged@example.com",
            groups=[],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=unprivileged_user):
            response = self.client.get(
                "/api/v1/membership/invitations/pending",
                data=self._datatables_query(length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 403)

    def test_pending_invitations_endpoint_returns_datatables_envelope(self) -> None:
        """Pending invitations list must return DataTables-format JSON."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
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
        accepted = AccountInvitation.objects.create(
            email="accepted@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
        )
        dismissed = AccountInvitation.objects.create(
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

    # === Accepted Invitations List Endpoint ===

    def test_accepted_invitations_endpoint_requires_authentication(self) -> None:
        """Anonymous users must be rejected with 403."""
        response = self.client.get(
            "/api/v1/membership/invitations/accepted",
            data=self._datatables_query(length=50),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_accepted_invitations_endpoint_returns_datatables_envelope(self) -> None:
        """Accepted invitations list must return DataTables-format JSON."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
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

        pending = AccountInvitation.objects.create(
            email="pending@example.com",
            invited_by_username="committee",
        )
        accepted = AccountInvitation.objects.create(
            email="accepted@example.com",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="accepteduser",
        )
        dismissed = AccountInvitation.objects.create(
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
        self._login_as_freeipa_user("unprivileged")
        unprivileged_user = self._make_freeipa_user("unprivileged")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=unprivileged_user):
            response = self.client.post(
                "/api/v1/membership/invitations/refresh",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 403)

    def test_refresh_endpoint_returns_ok_json(self) -> None:
        """Refresh must return {ok: true, message: ...} on success."""
        self._login_as_freeipa_user("committee")
        invitation = AccountInvitation.objects.create(
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
            patch("core.views_account_invitations.queue_templated_email", return_value=queued_email),
        ):
            response = self.client.post(
                f"/api/v1/membership/invitations/{invitation.pk}/resend",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("message", payload)

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
        self._login_as_freeipa_user("unprivileged")
        unprivileged_user = self._make_freeipa_user("unprivileged")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=unprivileged_user):
            response = self.client.post(
                "/api/v1/membership/invitations/bulk",
                data={
                    "bulk_action": "dismiss",
                    "selected": ["1"],
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 403)
