import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipLog, MembershipRequest, MembershipType, Organization
from core.permissions import ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipAuditLogApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
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

    def _datatables_query(self, *, length: int = 50) -> dict[str, str]:
        return {
            "draw": "7",
            "start": "0",
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "desc",
            "order[0][name]": "created_at",
            "columns[0][data]": "log_id",
            "columns[0][name]": "created_at",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

    def test_membership_audit_log_api_requires_permission(self) -> None:
        self._login_as_freeipa_user("unprivileged")
        unprivileged = self._make_freeipa_user("unprivileged", groups=[])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=unprivileged):
            response = self.client.get(
                "/api/v1/membership/audit-log",
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "Permission denied.")

    def test_membership_audit_log_api_returns_datatables_envelope_and_row_contract(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Contributions": "Patch submissions"}],
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            membership_request=req,
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.requested,
            created_at=timezone.now(),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                "/api/v1/membership/audit-log",
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["draw"], 7)
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(payload["recordsTotal"], 1)
        row = payload["data"][0]
        self.assertIn("log_id", row)
        self.assertIn("created_at_display", row)
        self.assertEqual(row["actor_username"], "reviewer")
        self.assertEqual(row["membership_name"], "Individual")
        self.assertEqual(row["action"], MembershipLog.Action.requested)
        self.assertEqual(row["action_display"], "Requested")
        self.assertEqual(row["target"]["kind"], "user")
        self.assertEqual(row["target"]["label"], "alice")
        self.assertEqual(row["request"]["request_id"], req.pk)
        self.assertEqual(row["request"]["responses"][0]["question"], "Contributions")
        self.assertIn("Patch submissions", row["request"]["responses"][0]["answer_html"])

    def test_membership_audit_log_api_filters_by_username_org_and_query(self) -> None:
        org = Organization.objects.create(name="Example Org", business_contact_email="contact@example.com")
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            created_at=timezone.now(),
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_organization=org,
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.terminated,
            created_at=timezone.now() + datetime.timedelta(seconds=1),
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp_username = self.client.get(
                "/api/v1/membership/audit-log",
                data={**self._datatables_query(), "username": "alice"},
                HTTP_ACCEPT="application/json",
            )
            resp_org = self.client.get(
                "/api/v1/membership/audit-log",
                data={**self._datatables_query(), "organization": str(org.pk)},
                HTTP_ACCEPT="application/json",
            )
            resp_query = self.client.get(
                "/api/v1/membership/audit-log",
                data={**self._datatables_query(), "q": "terminated"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp_username.status_code, 200)
        self.assertEqual(resp_username.json()["recordsFiltered"], 1)
        self.assertEqual(resp_username.json()["data"][0]["target"]["kind"], "user")

        self.assertEqual(resp_org.status_code, 200)
        self.assertEqual(resp_org.json()["recordsFiltered"], 1)
        self.assertEqual(resp_org.json()["data"][0]["target"]["kind"], "organization")

        self.assertEqual(resp_query.status_code, 200)
        self.assertEqual(resp_query.json()["recordsFiltered"], 1)
        self.assertEqual(resp_query.json()["data"][0]["action"], MembershipLog.Action.terminated)

    def test_membership_audit_log_api_rejects_invalid_query_parameters(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                "/api/v1/membership/audit-log",
                data={**self._datatables_query(), "unexpected": "1"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid query parameters.")

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_membership_audit_log_page_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(f"{reverse('membership-audit-log')}?q=alice")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-membership-audit-log-root")
        self.assertContains(response, 'data-membership-audit-log-api-url="/api/v1/membership/audit-log"')
        self.assertContains(response, 'data-membership-audit-log-page-size="50"')
        self.assertContains(response, 'data-membership-audit-log-initial-q="alice"')
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/membershipAuditLog.ts"')

    def test_legacy_membership_audit_log_routes_redirect_to_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response_user = self.client.get(reverse("membership-audit-log-user", kwargs={"username": "alice"}))
            response_org = self.client.get(
                reverse("membership-audit-log-organization", kwargs={"organization_id": 42})
            )

        self.assertEqual(response_user.status_code, 302)
        self.assertEqual(response_user.headers["Location"], "/membership/log/?username=alice")
        self.assertEqual(response_org.status_code, 302)
        self.assertEqual(response_org.headers["Location"], "/membership/log/?organization=42")
