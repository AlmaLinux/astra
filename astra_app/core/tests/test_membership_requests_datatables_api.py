import datetime
from unittest.mock import patch

from django.conf import settings
from django.db.models.query import QuerySet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from post_office.models import STATUS, Email, Log

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipLog, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipRequestsDataTablesApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
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

    def _login_as_committee(self, username: str = "reviewer") -> None:
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

    def _datatables_query(self, *, order_name: str, length: int) -> dict[str, str]:
        return {
            "draw": "3",
            "start": "0",
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "order[0][name]": order_name,
            "columns[0][data]": "request_id",
            "columns[0][name]": order_name,
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

    def test_enabled_page_shell_skips_shell_summary_and_lightweight_lookup(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=1),
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.membership_requests_datatables.build_membership_request_queue_summary",
                side_effect=AssertionError("membership requests shell must not build legacy queue-summary lists during page render"),
            ),
            patch(
                "core.views_membership.committee.build_membership_request_shell_summary",
                side_effect=AssertionError("membership requests shell must not build the shell summary during page render"),
                create=True,
            ),
            patch(
                "core.views_membership.committee.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=AssertionError("membership requests shell must not issue lightweight FreeIPA lookups during page render"),
            ),
            patch(
                "core.views_membership.committee.build_notes_by_membership_request_id",
                side_effect=AssertionError("membership requests shell must not preload SSR note widgets"),
            ),
        ):
            response = self.client.get(f"{reverse('membership-requests')}?filter=renewals")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-membership-requests-root')
        self.assertContains(response, 'data-membership-requests-next-url="/membership/requests/?filter=renewals"')
        self.assertContains(response, 'src/entrypoints/membershipRequests.ts')
        self.assertNotContains(response, "All (2)")

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
    def test_membership_requests_page_renders_vue_asset_hook_when_vue_route_enabled(self) -> None:
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("membership-requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-membership-requests-root')
        self.assertContains(response, 'src="http://localhost:5173/static/@vite/client"')
        self.assertContains(response, 'src="http://localhost:5173/static/src/entrypoints/membershipRequests.ts"')
        self.assertNotContains(response, 'http://localhost:5173/static/bundler/@vite/client')
        self.assertNotContains(response, 'http://localhost:5173/static/bundler/src/entrypoints/membershipRequests.ts')
        self.assertNotContains(response, "core/js/membership_notes.js")
        self.assertNotContains(response, "core/js/membership_requests_datatables.js")

    def test_pending_endpoint_does_not_query_on_hold_queue(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=1),
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        users = [
            reviewer,
            self._make_freeipa_user("alice", email="alice@example.com"),
            self._make_freeipa_user("bob", email="bob@example.com"),
        ]
        self._login_as_committee()

        original_filter = QuerySet.filter

        def fail_on_on_hold_filter(queryset: QuerySet, *args: object, **kwargs: object):
            if queryset.model is MembershipRequest and kwargs.get("status") == MembershipRequest.Status.on_hold:
                raise AssertionError("pending endpoint should not rebuild the on-hold queue")
            return original_filter(queryset, *args, **kwargs)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in users if user.username},
            ),
            patch("django.db.models.query.QuerySet.filter", autospec=True, side_effect=fail_on_on_hold_filter),
        ):
            response = self.client.get(
                "/api/v1/membership/requests/pending",
                data={
                    **self._datatables_query(order_name="requested_at", length=25),
                    "queue_filter": "all",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)

    def test_on_hold_endpoint_does_not_query_pending_queue(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=1),
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        users = [
            reviewer,
            self._make_freeipa_user("alice", email="alice@example.com"),
            self._make_freeipa_user("bob", email="bob@example.com"),
        ]
        self._login_as_committee()

        original_filter = QuerySet.filter

        def fail_on_pending_filter(queryset: QuerySet, *args: object, **kwargs: object):
            if queryset.model is MembershipRequest and kwargs.get("status") == MembershipRequest.Status.pending:
                raise AssertionError("on-hold endpoint should not rebuild the pending queue")
            return original_filter(queryset, *args, **kwargs)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in users if user.username},
            ),
            patch("django.db.models.query.QuerySet.filter", autospec=True, side_effect=fail_on_pending_filter),
        ):
            response = self.client.get(
                "/api/v1/membership/requests/on-hold",
                data=self._datatables_query(order_name="on_hold_at", length=10),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)

    def test_pending_endpoint_returns_data_only_datatables_envelope(self) -> None:
        membership_type = MembershipType.objects.get(code="individual")
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipLog.objects.create(
            actor_username="charlie",
            target_username="alice",
            membership_type=membership_type,
            membership_request=pending_request,
            action=MembershipLog.Action.requested,
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        alice = self._make_freeipa_user("alice", email="alice@example.com")
        charlie = self._make_freeipa_user("charlie", email="charlie@example.com")

        self._login_as_committee()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in [reviewer, alice, charlie] if user.username},
            ),
        ):
            response = self.client.get(
                "/api/v1/membership/requests/pending",
                data={
                    **self._datatables_query(order_name="requested_at", length=25),
                    "queue_filter": "all",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["draw"], 3)
        self.assertEqual(payload["recordsTotal"], 1)
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(
            payload["pending_filter"],
            {
                "selected": "all",
                "options": [
                    {"value": "all", "label": "All", "count": 1},
                    {"value": "renewals", "label": "Renewals", "count": 0},
                    {"value": "sponsorships", "label": "Sponsorships", "count": 0},
                    {"value": "individuals", "label": "Individuals", "count": 1},
                    {"value": "mirrors", "label": "Mirrors", "count": 0},
                ],
            },
        )
        self.assertEqual(len(payload["data"]), 1)
        row = payload["data"][0]

        self.assertEqual(row["request_id"], pending_request.pk)
        self.assertEqual(row["requested_at"], pending_request.requested_at.isoformat())
        self.assertEqual(row["requested_by"]["username"], "charlie")
        self.assertNotIn("detail_url", row)
        self.assertNotIn("action_urls", row)
        self.assertNotIn("capabilities", row)
        self.assertNotIn("url", row["target"])
        self.assertNotIn("url", row["requested_by"])
        self.assertNotIn("compact_notes", row)
        self.assertNotIn("note_summary", row)
        self.assertNotIn("note_details", row)

    def test_membership_request_note_summary_endpoint_returns_summary_only(self) -> None:
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            action={"type": "vote", "value": "approve"},
        )
        Note.objects.create(
            membership_request=pending_request,
            username="second-reviewer",
            action={"type": "vote", "value": "disapprove"},
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            content="Need one more check.",
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                f"/api/v1/membership/notes/{pending_request.pk}/summary",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "note_count": 3,
                "approvals": 1,
                "disapprovals": 1,
                "current_user_vote": "approve",
            },
        )

    def test_membership_request_note_summary_api_does_not_use_preload_or_detail_paths(self) -> None:
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            action={"type": "vote", "value": "disapprove"},
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            content="Need one more check.",
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            action={"type": "vote", "value": "approve"},
        )
        Note.objects.create(
            membership_request=pending_request,
            username="second-reviewer",
            action={"type": "vote", "value": "disapprove"},
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.views_membership.committee.build_notes_by_membership_request_id",
                side_effect=AssertionError("request notes summary must not use the preload helper"),
            ),
            patch(
                "core.views_membership.committee.build_note_details",
                side_effect=AssertionError("request notes summary must not use detail builders"),
            ),
        ):
            response = self.client.get(
                reverse("api-membership-request-notes-summary", args=[pending_request.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "note_count": 4,
                "approvals": 1,
                "disapprovals": 1,
                "current_user_vote": "approve",
            },
        )

    def test_membership_request_note_read_endpoints_return_json_for_anonymous_access(self) -> None:
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        for url in (
            reverse("api-membership-request-notes-summary", args=[pending_request.pk]),
            reverse("api-membership-request-notes", args=[pending_request.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_ACCEPT="application/json")

                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["Content-Type"], "application/json")
                self.assertEqual(response.json(), {"ok": False, "error": "Authentication required."})

    def test_membership_request_note_read_endpoints_return_json_for_unauthorized_access(self) -> None:
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[],
        )

        self._login_as_committee()

        for url in (
            reverse("api-membership-request-notes-summary", args=[pending_request.pk]),
            reverse("api-membership-request-notes", args=[pending_request.pk]),
        ):
            with self.subTest(url=url):
                with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
                    response = self.client.get(url, HTTP_ACCEPT="application/json")

                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["Content-Type"], "application/json")
                self.assertEqual(response.json(), {"error": "Permission denied."})

    def test_membership_request_note_read_endpoints_return_json_for_missing_request(self) -> None:
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        for url in (
            reverse("api-membership-request-notes-summary", args=[999999]),
            reverse("api-membership-request-notes", args=[999999]),
        ):
            with self.subTest(url=url):
                with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
                    response = self.client.get(url, HTTP_ACCEPT="application/json")

                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["Content-Type"], "application/json")
                self.assertEqual(response.json(), {"error": "Membership request not found."})

    def test_membership_request_note_detail_api_omits_aggregate_only_request_link_metadata(self) -> None:
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            content="Request detail note",
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[pending_request.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["groups"]), 1)
        self.assertNotIn("membership_request_id", payload["groups"][0])
        self.assertNotIn("membership_request_url", payload["groups"][0])

    def test_on_hold_endpoint_returns_datatables_envelope(self) -> None:
        on_hold_request = MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=3),
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        bob = self._make_freeipa_user("bob", email="bob@example.com")

        self._login_as_committee()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in [reviewer, bob] if user.username},
            ),
        ):
            response = self.client.get(
                "/api/v1/membership/requests/on-hold",
                data=self._datatables_query(order_name="on_hold_at", length=10),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["draw"], 3)
        self.assertEqual(payload["recordsTotal"], 1)
        self.assertEqual(payload["recordsFiltered"], 1)
        self.assertEqual(len(payload["data"]), 1)
        row = payload["data"][0]

        self.assertEqual(row["request_id"], on_hold_request.pk)
        self.assertEqual(row["status"], MembershipRequest.Status.on_hold)
        self.assertEqual(row["on_hold_since"], on_hold_request.on_hold_at.isoformat())
        self.assertNotIn("detail_url", row)
        self.assertNotIn("action_urls", row)
        self.assertNotIn("capabilities", row)
        self.assertNotIn("compact_notes", row)

    def test_membership_request_note_details_endpoint_returns_grouped_content_only(self) -> None:
        pending_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            subject="Approval notice",
            message="Plain text body",
            html_message="<p>HTML body</p>",
            headers={"Reply-To": "committee@example.com"},
        )
        Log.objects.create(
            email=email,
            status=STATUS.sent,
            message="sent",
            exception_type="",
        )
        Note.objects.create(
            membership_request=pending_request,
            username="reviewer",
            action={"type": "contacted", "kind": "approved", "email_id": email.id},
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        alice = self._make_freeipa_user("alice", email="alice@example.com")

        self._login_as_committee()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in [reviewer, alice] if user.username},
            ),
        ):
            response = self.client.get(
                f"/api/v1/membership/notes/{pending_request.pk}",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("groups", payload)
        self.assertNotIn("note_count", payload)
        self.assertNotIn("approvals", payload)
        self.assertNotIn("disapprovals", payload)
        self.assertNotIn("current_user_vote", payload)
        contacted_email = payload["groups"][0]["entries"][0]["contacted_email"]

        self.assertEqual(contacted_email["email_id"], email.id)
        self.assertEqual(contacted_email["subject"], "Approval notice")
        self.assertEqual(contacted_email["to"], ["alice@example.com"])
        self.assertEqual(contacted_email["from_email"], "noreply@example.com")
        self.assertEqual(contacted_email["reply_to"], "committee@example.com")
        self.assertEqual(contacted_email["html"], "<p>HTML body</p>")
        self.assertEqual(contacted_email["text"], "Plain text body")

    def test_pending_endpoint_rejects_undocumented_query_parameter(self) -> None:
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                "/api/v1/membership/requests/pending",
                data={
                    **self._datatables_query(order_name="requested_at", length=25),
                    "queue_filter": "all",
                    "unexpected": "1",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid query parameters.")

    def test_pending_endpoint_rejects_non_positive_or_too_large_length(self) -> None:
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        self._login_as_committee()

        invalid_lengths = (0, 101)
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            for length in invalid_lengths:
                with self.subTest(length=length):
                    response = self.client.get(
                        "/api/v1/membership/requests/pending",
                        data={
                            **self._datatables_query(order_name="requested_at", length=length),
                            "queue_filter": "all",
                        },
                        HTTP_ACCEPT="application/json",
                    )

                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["error"], "Invalid query parameters.")

    def test_pending_endpoint_accepts_length_up_to_100(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        alice = self._make_freeipa_user("alice", email="alice@example.com")

        self._login_as_committee()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.freeipa.user.FreeIPAUser.find_lightweight_by_usernames",
                return_value={user.username: user for user in [reviewer, alice] if user.username},
            ),
        ):
            response = self.client.get(
                "/api/v1/membership/requests/pending",
                data={
                    **self._datatables_query(order_name="requested_at", length=100),
                    "queue_filter": "all",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recordsFiltered"], 1)

    def test_pending_endpoint_rejects_descending_order_it_does_not_honor(self) -> None:
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                "/api/v1/membership/requests/pending",
                data={
                    **self._datatables_query(order_name="requested_at", length=25),
                    "queue_filter": "all",
                    "order[0][dir]": "desc",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid query parameters.")

    def test_pending_endpoint_rejects_request_matrix_variants_outside_the_frozen_client_shape(self) -> None:
        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        self._login_as_committee()

        invalid_overrides = (
            {"order[0][column]": "1"},
            {"columns[0][searchable]": "false"},
            {"columns[0][orderable]": "false"},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            for overrides in invalid_overrides:
                with self.subTest(overrides=overrides):
                    response = self.client.get(
                        "/api/v1/membership/requests/pending",
                        data={
                            **self._datatables_query(order_name="requested_at", length=25),
                            "queue_filter": "all",
                            **overrides,
                        },
                        HTTP_ACCEPT="application/json",
                    )

                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["error"], "Invalid query parameters.")

    def test_membership_requests_page_renders_datatables_shell(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=1),
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                f"{reverse('membership-requests')}?filter=renewals&pending_page=2&on_hold_page=3"
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-membership-requests-root')
        self.assertContains(response, '/api/v1/membership/requests/pending')
        self.assertContains(response, '/api/v1/membership/requests/on-hold')
        self.assertContains(response, 'src/entrypoints/membershipRequests.ts')
        self.assertContains(response, 'data-membership-request-detail-template=')
        self.assertContains(response, 'data-membership-request-approve-template=')
        self.assertContains(response, 'data-membership-request-approve-on-hold-template=')
        self.assertContains(response, 'data-membership-request-reject-template=')
        self.assertContains(response, 'data-membership-request-rfi-template=')
        self.assertContains(response, 'data-membership-request-ignore-template=')
        self.assertContains(response, 'data-membership-request-note-add-template=')
        self.assertContains(response, 'data-membership-request-note-summary-template=')
        self.assertContains(response, 'data-membership-request-note-detail-template=')
        self.assertContains(response, 'data-membership-user-profile-template=')
        self.assertContains(response, 'data-membership-organization-detail-template=')
        self.assertNotContains(response, 'data-membership-requests-selected-filter=')
        self.assertContains(response, 'name="next" value="/membership/requests/?filter=renewals&amp;pending_page=2&amp;on_hold_page=3"')
        self.assertContains(response, 'data-membership-requests-notes-can-view="true"')
        self.assertContains(response, 'data-membership-requests-notes-can-write="true"')
        self.assertContains(response, 'data-membership-requests-notes-can-vote="true"')
        self.assertNotContains(response, 'onchange="this.form.submit()"')
        self.assertNotContains(response, 'Request #1')

    def test_membership_requests_page_always_renders_vue_shell(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        reviewer = self._make_freeipa_user(
            "reviewer",
            email="reviewer@example.com",
            groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        self._login_as_committee()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("membership-requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-membership-requests-root')
        self.assertContains(response, 'src/entrypoints/membershipRequests.ts')
        self.assertNotContains(response, "Request #1")


