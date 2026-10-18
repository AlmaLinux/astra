import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipLog, MembershipRequest, MembershipType, Organization
from core.permissions import ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import FAR_FUTURE, ensure_core_categories


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
        self.assertNotIn("url", row["target"])
        self.assertEqual(row["request"]["request_id"], req.pk)
        self.assertNotIn("url", row["request"])
        self.assertEqual(row["request"]["responses"][0]["question"], "Contributions")
        self.assertIn("Patch submissions", row["request"]["responses"][0]["answer_html"])

    def test_membership_audit_log_api_filters_by_username_org_and_query(self) -> None:
        org = Organization.objects.create(name="Example Org", business_contact_email="contact@example.com")
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            action=MembershipLog.Action.approved,
            expires_at=FAR_FUTURE,
            created_at=timezone.now(),
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_organization=org,
            membership_type_id="individual",
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

    def test_membership_audit_log_detail_api_gives_each_action_its_own_details(self) -> None:
        from core.models import MembershipTerminationFeedback

        gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold", "group_cn": "almalinux-gold", "category_id": "sponsorship", "enabled": True,
                "annual_dues_usd": 20000,
            },
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        req = MembershipRequest.objects.create(
            requested_organization=org,
            membership_type=gold,
            responses=[{"Why": "We ship AlmaLinux"}],
        )
        old_expiry = FAR_FUTURE - datetime.timedelta(days=30)

        def log(action: str, **fields: object) -> MembershipLog:
            return MembershipLog.objects.create(
                actor_username="committee", target_organization=org, membership_type=gold,
                membership_request=req, action=action, **fields,
            )

        requested = log(MembershipLog.Action.requested)
        approved = log(MembershipLog.Action.approved, expires_at=old_expiry)
        on_hold = log(MembershipLog.Action.on_hold, rejection_reason="Which products?")
        rejected = log(MembershipLog.Action.rejected, rejection_reason="Not a sponsor")
        expiry = log(MembershipLog.Action.expiry_changed, expires_at=FAR_FUTURE, previous_expires_at=old_expiry)
        # previous_dues_usd None: the sponsor was paying the tier's standard price.
        dues = log(MembershipLog.Action.dues_changed, expires_at=FAR_FUTURE, dues_usd=5000)
        terminated = MembershipLog.objects.create(
            actor_username="alice", target_username="alice", membership_type_id="individual",
            action=MembershipLog.Action.terminated, expires_at=FAR_FUTURE,
        )
        MembershipTerminationFeedback.objects.create(
            membership_log=terminated,
            reason_category=MembershipTerminationFeedback.ReasonCategory.privacy,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user("reviewer", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-audit-log-detail"),
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        rows = {row["log_id"]: row for row in response.json()["data"]}
        details = ("expires_at", "reason", "previous_expires_at", "dues", "termination_reason_category")

        def row_details(entry: MembershipLog) -> dict[str, object]:
            row = rows[entry.pk]
            return {
                "responses": row["request"]["responses"] if row["request"] else None,
                **{key: row[key] for key in details},
            }

        empty = {
            "expires_at": None, "reason": None, "previous_expires_at": None, "dues": None,
            "termination_reason_category": None,
        }
        responses = [{"question": "Why", "answer_text": "We ship AlmaLinux", "segments": [{"kind": "text", "text": "We ship AlmaLinux"}]}]
        expected = {
            # Only the request's own actions carry its answers; later actions just link to it.
            requested: {**empty, "responses": responses},
            # The term end only matters where a term is set; a dues change keeps it and a
            # termination's expires_at is just when it happened.
            approved: {**empty, "responses": [], "expires_at": old_expiry.isoformat()},
            on_hold: {**empty, "responses": [], "reason": "Which products?"},
            rejected: {**empty, "responses": [], "reason": "Not a sponsor"},
            expiry: {
                **empty, "responses": [], "expires_at": FAR_FUTURE.isoformat(), "previous_expires_at": old_expiry.isoformat(),
            },
            dues: {**empty, "responses": [], "dues": {"old_usd": 20000, "new_usd": 5000, "default_usd": 20000}},
            terminated: {**empty, "responses": None, "termination_reason_category": "privacy"},
        }
        for entry, entry_details in expected.items():
            with self.subTest(action=entry.action):
                self.assertEqual(row_details(entry), entry_details)

    def test_membership_audit_log_search_finds_invoice_numbers_and_representatives(self) -> None:
        gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={"name": "Gold", "group_cn": "almalinux-gold", "category_id": "sponsorship", "enabled": True},
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        invoice_log = MembershipLog.objects.create(
            actor_username="committee",
            target_organization=org,
            membership_type=gold,
            action=MembershipLog.Action.invoice_generated,
            invoice_number="0001-2026-123456",
        )
        representative_log = MembershipLog.objects.create(
            actor_username="committee",
            target_organization=org,
            membership_type=gold,
            action=MembershipLog.Action.representative_changed,
            old_representative="carol",
            new_representative="bob",
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user("reviewer", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        # A sponsor quoting an invoice number, or a former representative, must lead back to the log.
        for q, expected in (("2026-123456", invoice_log), ("carol", representative_log)):
            with self.subTest(q=q), patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
                response = self.client.get(
                    reverse("api-membership-audit-log-detail"),
                    data={**self._datatables_query(), "q": q},
                    HTTP_ACCEPT="application/json",
                )
                self.assertEqual([row["log_id"] for row in response.json()["data"]], [expected.pk])

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

    def test_membership_audit_log_detail_api_returns_data_only_row_contract(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Domain": "mirror.example.org"}],
        )
        created_at = timezone.now()
        expires_at = created_at + datetime.timedelta(days=30)
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            membership_request=req,
            action=MembershipLog.Action.requested,
            created_at=created_at,
            expires_at=expires_at,
        )
        log = MembershipLog.objects.get()

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-audit-log-detail"),
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["created_at"], log.created_at.isoformat())
        # A request sets no term, so its expires_at is not part of the row.
        self.assertIsNone(row["expires_at"])
        self.assertEqual(row["action"], MembershipLog.Action.requested)
        self.assertNotIn("created_at_display", row)
        self.assertNotIn("action_display", row)
        self.assertNotIn("expires_display", row)
        self.assertEqual(
            row["request"]["responses"][0],
            {
                "question": "Domain",
                "answer_text": "mirror.example.org",
                "segments": [
                    {
                        "kind": "link",
                        "text": "mirror.example.org",
                        "url": "https://mirror.example.org",
                    }
                ],
            },
        )
        self.assertNotIn("answer_html", row["request"]["responses"][0])

    def test_membership_audit_log_detail_api_links_generated_invoices(self) -> None:
        from core.sponsorship_invoices import invoice_storage_key

        gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={"name": "Gold", "group_cn": "almalinux-gold", "category_id": "sponsorship", "enabled": True},
        )
        org = Organization.objects.create(name="Acme", representative="rep1")
        MembershipLog.objects.create(
            actor_username="rep1",
            target_organization=org,
            membership_type=gold,
            action=MembershipLog.Action.invoice_regenerated,
            invoice_number="0001-2026-123456",
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_organization=org,
            membership_type=gold,
            action=MembershipLog.Action.requested,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user("reviewer", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-audit-log-detail"),
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        invoice_by_action = {row["action"]: row["invoice"] for row in response.json()["data"]}
        self.assertEqual(
            invoice_by_action,
            {
                MembershipLog.Action.invoice_regenerated: {
                    "number": "0001-2026-123456",
                    # Storage URLs can't be built by the UI, so the API hands it over like the org page does.
                    "url": default_storage.url(invoice_storage_key(org, "0001-2026-123456")),
                },
                MembershipLog.Action.requested: None,
            },
        )

    def test_membership_audit_log_detail_api_includes_representative_change(self) -> None:
        gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={"name": "Gold", "group_cn": "almalinux-gold", "category_id": "sponsorship", "enabled": True},
        )
        org = Organization.objects.create(name="Acme", representative="bob")
        MembershipLog.objects.create(
            actor_username="committee",
            target_organization=org,
            membership_type=gold,
            action=MembershipLog.Action.representative_changed,
            old_representative="carol",
            new_representative="bob",
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_organization=org,
            membership_type=gold,
            action=MembershipLog.Action.requested,
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._make_freeipa_user("reviewer", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-audit-log-detail"),
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["action"]: row["representative"] for row in response.json()["data"]},
            {
                MembershipLog.Action.representative_changed: {"old": "carol", "new": "bob"},
                MembershipLog.Action.requested: None,
            },
        )

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
        self.assertContains(response, f'data-membership-audit-log-api-url="{reverse("api-membership-audit-log-detail")}"')
        self.assertContains(response, 'data-membership-audit-log-page-size="50"')
        self.assertContains(response, 'data-membership-audit-log-initial-q="alice"')
        self.assertContains(response, 'data-membership-audit-log-user-profile-url-template="/user/__username__/"')
        self.assertContains(response, 'data-membership-audit-log-organization-detail-url-template="/organization/__organization_id__/"')
        self.assertContains(response, 'data-membership-audit-log-membership-request-detail-url-template="/membership/request/__request_id__/"')
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
