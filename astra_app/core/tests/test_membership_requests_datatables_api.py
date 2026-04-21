import datetime
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase
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
        self.assertContains(response, "Pending: --")
        self.assertContains(response, "On hold: --")
        self.assertContains(response, "All")
        self.assertContains(response, "Renewals")
        self.assertContains(response, 'option value="renewals" selected')
        self.assertNotContains(response, "All (2)")

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
                    **self._datatables_query(order_name="requested_at", length=50),
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
                    **self._datatables_query(order_name="requested_at", length=50),
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
                f"/api/v1/membership/requests/{pending_request.pk}/notes/summary",
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
                f"/api/v1/membership/requests/{pending_request.pk}/notes",
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
                    **self._datatables_query(order_name="requested_at", length=50),
                    "queue_filter": "all",
                    "unexpected": "1",
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid query parameters.")

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
                    **self._datatables_query(order_name="requested_at", length=50),
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
                            **self._datatables_query(order_name="requested_at", length=50),
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
        self.assertContains(response, 'src="/static/core/js/membership_requests_datatables.js"')
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
        self.assertContains(response, 'Loading pending requests...')
        self.assertContains(response, 'Loading on-hold requests...')
        self.assertContains(response, 'id="membership-requests-pending-info"')
        self.assertContains(response, 'id="membership-requests-pending-pager"')
        self.assertContains(response, 'id="membership-requests-on-hold-info"')
        self.assertContains(response, 'id="membership-requests-on-hold-pager"')
        self.assertNotContains(response, 'onchange="this.form.submit()"')
        self.assertNotContains(response, 'Request #1')

    def test_membership_requests_page_always_renders_datatables_shell(self) -> None:
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
        self.assertContains(response, 'src="/static/core/js/membership_requests_datatables.js"')
        self.assertNotContains(response, "Request #1")


class MembershipRequestsDataTablesJsContractTests(SimpleTestCase):
    def test_container_environment_includes_node_for_js_execution_tests(self) -> None:
        self.assertIsNotNone(
            shutil.which("node"),
            "Node.js must be available in the repo-standard test environment so the checked-in JS execution tests run in-container.",
        )

    def test_script_clears_both_selection_scopes_on_canonical_page_changes(self) -> None:
        script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_requests_datatables.js"
        source = script_path.read_text(encoding="utf-8")

        self.assertIn("function clearAllSelections()", source)
        self.assertIn("selectedBySection.pending = {};", source)
        self.assertIn("selectedBySection.on_hold = {};", source)
        self.assertIn("$(tableSelector).on('page.dt', function () {", source)
        self.assertIn("clearAllSelections();", source)

    def test_script_recomputes_post_next_from_the_canonical_browser_url(self) -> None:
        script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_requests_datatables.js"
        source = script_path.read_text(encoding="utf-8")

        self.assertIn("function currentNextUrl()", source)
        self.assertIn("return window.location.pathname + window.location.search;", source)
        self.assertIn("nextInput.value = currentNextUrl();", source)

    def test_script_declares_summary_fetch_and_lazy_detail_fetch_contract(self) -> None:
        script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_requests_datatables.js"
        source = script_path.read_text(encoding="utf-8")

        self.assertIn("function loadNoteSummary(", source)
        self.assertIn("function loadNoteDetails(", source)
        self.assertIn("noteSummaryTemplate", source)
        self.assertIn("noteDetailTemplate", source)
        self.assertIn("detailsLoadedByRequestId", source)


@skipUnless(shutil.which("node"), "Node.js is required for membership requests DataTables JS execution tests.")
class MembershipRequestsDataTablesJsExecutionTests(SimpleTestCase):
        maxDiff = None

        def _run_node_scenario(self, test_body: str) -> None:
                script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_requests_datatables.js"
                source = script_path.read_text(encoding="utf-8")

                node_script = textwrap.dedent(
                        f"""
                        const vm = require('vm');

                        const source = {json.dumps(source)};

                        class EventTarget {{
                            constructor() {{
                                this._listeners = Object.create(null);
                            }}

                            addEventListener(type, handler) {{
                                this._listeners[type] = this._listeners[type] || [];
                                this._listeners[type].push(handler);
                            }}

                            dispatchEvent(event) {{
                                event.target = event.target || this;
                                event.currentTarget = this;
                                const handlers = this._listeners[event.type] || [];
                                handlers.forEach((handler) => handler.call(this, event));
                                return !event.defaultPrevented;
                            }}
                        }}

                        class CustomEventStub {{
                            constructor(type, options) {{
                                this.type = type;
                                this.detail = options && options.detail ? options.detail : {{}};
                                this.bubbles = !!(options && options.bubbles);
                                this.cancelable = !!(options && options.cancelable);
                                this.defaultPrevented = false;
                            }}

                            preventDefault() {{
                                if (this.cancelable) {{
                                    this.defaultPrevented = true;
                                }}
                            }}
                        }}

                        class ElementStub extends EventTarget {{
                            constructor(documentRef, options) {{
                                super();
                                this.ownerDocument = documentRef;
                                this.id = options.id || '';
                                this.tagName = options.tagName || 'div';
                                this.attributes = Object.assign({{}}, options.attributes || {{}});
                                this.className = options.className || this.attributes.class || '';
                                this.value = options.value || '';
                                this.checked = !!options.checked;
                                this.disabled = !!options.disabled;
                                this.parentNode = options.parentNode || null;
                                this.children = [];
                                this._innerHTML = '';
                            }}

                            get classList() {{
                                const element = this;
                                return {{
                                    contains(className) {{
                                        return element.className.split(/\\s+/).includes(className);
                                    }},
                                }};
                            }}

                            appendChild(child) {{
                                child.parentNode = this;
                                this.children.push(child);
                                return child;
                            }}

                            getAttribute(name) {{
                                if (name === 'id') return this.id;
                                if (name === 'class') return this.className;
                                return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
                            }}

                            setAttribute(name, value) {{
                                if (name === 'id') {{
                                    this.id = String(value);
                                    return;
                                }}
                                if (name === 'class') {{
                                    this.className = String(value);
                                    return;
                                }}
                                this.attributes[name] = String(value);
                            }}

                            matches(selector) {{
                                return selector.split(',').map((part) => part.trim()).some((part) => this._matchesOne(part));
                            }}

                            _matchesOne(selector) {{
                                if (!selector) return false;
                                if (selector.startsWith('#')) return this.id === selector.slice(1);
                                if (selector.startsWith('.')) return this.className.split(/\\s+/).includes(selector.slice(1));
                                if (selector === '[data-membership-notes-form]') return this.getAttribute('data-membership-notes-form') !== null;
                                return false;
                            }}

                            closest(selector) {{
                                let current = this;
                                while (current) {{
                                    if (typeof current.matches === 'function' && current.matches(selector)) {{
                                        return current;
                                    }}
                                    current = current.parentNode;
                                }}
                                return null;
                            }}

                            querySelector(selector) {{
                                if (selector === 'input[name="next"]') {{
                                    return this.children.find((child) => child.tagName === 'input' && child.getAttribute('name') === 'next') || null;
                                }}
                                if (selector === 'select[name="bulk_action"]') {{
                                    return this.children.find((child) => child.tagName === 'select' && child.getAttribute('name') === 'bulk_action') || null;
                                }}
                                const attributeMatch = selector.match(/^\\[([^=\\]]+)="([^"]*)"\\]$/);
                                if (attributeMatch) {{
                                    return this._findFirstByAttribute(attributeMatch[1], attributeMatch[2]);
                                }}
                                return null;
                            }}

                            querySelectorAll(selector) {{
                                if (selector === 'button') {{
                                    return this.children.filter((child) => child.tagName === 'button');
                                }}
                                return [];
                            }}

                            _findFirstByAttribute(attributeName, attributeValue) {{
                                for (const child of this.children) {{
                                    if (child.getAttribute(attributeName) === attributeValue) {{
                                        return child;
                                    }}
                                    const nestedMatch = child._findFirstByAttribute(attributeName, attributeValue);
                                    if (nestedMatch) {{
                                        return nestedMatch;
                                    }}
                                }}
                                return null;
                            }}

                            set innerHTML(value) {{
                                this._innerHTML = String(value);
                                if (this.tagName === 'tbody') {{
                                    this.ownerDocument.updateTableBody(this, this._innerHTML);
                                }}
                            }}

                            get innerHTML() {{
                                return this._innerHTML;
                            }}
                        }}

                        class DocumentStub extends EventTarget {{
                            constructor() {{
                                super();
                                this.readyState = 'complete';
                                this._elementsById = Object.create(null);
                                this._pendingCheckboxes = [];
                                this._onHoldCheckboxes = [];
                                this._dynamicElementIds = [];
                                this._root = null;
                                this._pendingTbody = null;
                                this._onHoldTbody = null;
                                this._csrfInput = null;
                            }}

                            register(element) {{
                                if (element.id) {{
                                    this._elementsById[element.id] = element;
                                }}
                                return element;
                            }}

                            createElement(tagName) {{
                                return new ElementStub(this, {{ tagName }});
                            }}

                            getElementById(id) {{
                                return this._elementsById[id] || null;
                            }}

                            querySelector(selector) {{
                                if (selector === '[data-membership-requests-root]') return this._root;
                                if (selector === '#membership-requests-pending-table tbody') return this._pendingTbody;
                                if (selector === '#membership-requests-on-hold-table tbody') return this._onHoldTbody;
                                if (selector === 'input[name="csrfmiddlewaretoken"]') return this._csrfInput;
                                if (selector.startsWith('#')) return this.getElementById(selector.slice(1));
                                return null;
                            }}

                            querySelectorAll(selector) {{
                                if (selector === '.request-checkbox--pending') return this._pendingCheckboxes.slice();
                                if (selector === '.request-checkbox--on-hold') return this._onHoldCheckboxes.slice();
                                if (selector === '.request-checkbox--pending, .request-checkbox--on-hold') {{
                                    return this._pendingCheckboxes.concat(this._onHoldCheckboxes);
                                }}
                                return [];
                            }}

                            updateTableBody(tbody, html) {{
                                this._dynamicElementIds.forEach((elementId) => {{
                                    delete this._elementsById[elementId];
                                }});
                                this._dynamicElementIds = [];
                                const inputRegex = /<input type="checkbox" class="([^"]+)" name="selected" value="([^"]+)" form="([^"]+)" aria-label="Select request"( checked)?>/g;
                                const noteContainerRegex = /<div id="membership-notes-container-([^"]+)"([^>]*)>/g;
                                const checkboxes = [];
                                let match;
                                while ((match = inputRegex.exec(html)) !== null) {{
                                    const checkbox = new ElementStub(this, {{
                                        tagName: 'input',
                                        className: match[1],
                                        value: match[2],
                                        checked: !!match[4],
                                        attributes: {{
                                            name: 'selected',
                                            form: match[3],
                                        }},
                                    }});
                                    checkboxes.push(checkbox);
                                }}

                                while ((match = noteContainerRegex.exec(html)) !== null) {{
                                    const requestId = match[1];
                                    const attributeSource = match[2] || '';
                                    const attributes = {{}};
                                    attributeSource.replace(/\\s(data-[^=\\s]+)="([^"]*)"/g, (_fullMatch, key, value) => {{
                                        attributes[key] = value;
                                        return '';
                                    }});

                                    const container = this.register(new ElementStub(this, {{
                                        id: 'membership-notes-container-' + requestId,
                                        tagName: 'div',
                                        attributes,
                                    }}));
                                    this._dynamicElementIds.push(container.id);

                                    const card = this.register(new ElementStub(this, {{
                                        id: 'membership-notes-card-' + requestId,
                                        tagName: 'div',
                                        className: 'card card-primary card-outline direct-chat direct-chat-primary mb-0 collapsed-card',
                                        attributes: {{ 'data-membership-notes-card': requestId }},
                                    }}));
                                    this._dynamicElementIds.push(card.id);
                                    container.appendChild(card);

                                    const header = this.register(new ElementStub(this, {{
                                        id: 'membership-notes-header-' + requestId,
                                        tagName: 'div',
                                        className: 'card-header membership-notes-header-compact',
                                        attributes: {{ 'data-membership-notes-header': requestId }},
                                    }}));
                                    this._dynamicElementIds.push(header.id);
                                    container.appendChild(header);

                                    container.appendChild(new ElementStub(this, {{
                                        tagName: 'button',
                                        attributes: {{ 'data-membership-notes-collapse': requestId }},
                                    }}));
                                    container.appendChild(new ElementStub(this, {{
                                        tagName: 'span',
                                        attributes: {{ 'data-membership-notes-count': requestId }},
                                    }}));
                                    container.appendChild(new ElementStub(this, {{
                                        tagName: 'span',
                                        attributes: {{ 'data-membership-notes-approvals': requestId }},
                                    }}));
                                    container.appendChild(new ElementStub(this, {{
                                        tagName: 'span',
                                        attributes: {{ 'data-membership-notes-disapprovals': requestId }},
                                    }}));
                                    container.appendChild(new ElementStub(this, {{
                                        tagName: 'div',
                                        attributes: {{ 'data-membership-notes-messages': requestId }},
                                    }}));
                                    container.appendChild(new ElementStub(this, {{
                                        tagName: 'div',
                                        attributes: {{ 'data-membership-notes-modals': requestId }},
                                    }}));
                                }}

                                if (tbody === this._pendingTbody) {{
                                    this._pendingCheckboxes = checkboxes;
                                }} else if (tbody === this._onHoldTbody) {{
                                    this._onHoldCheckboxes = checkboxes;
                                }}
                            }}
                        }}

                        const document = new DocumentStub();

                        function registerElement(options) {{
                            return document.register(new ElementStub(document, options));
                        }}

                        const root = registerElement({{
                            attributes: {{
                                'data-membership-requests-root': '',
                                'data-membership-requests-pending-api-url': '/api/v1/membership/requests/pending',
                                'data-membership-requests-on-hold-api-url': '/api/v1/membership/requests/on-hold',
                                'data-membership-requests-clear-filter-url': '/membership/requests/',
                                'data-membership-request-id-sentinel': '123456789',
                                'data-membership-request-detail-template': '/membership/request/123456789/',
                                'data-membership-request-approve-template': '/membership/requests/123456789/approve/',
                                'data-membership-request-approve-on-hold-template': '/membership/requests/123456789/approve-on-hold/',
                                'data-membership-request-reject-template': '/membership/requests/123456789/reject/',
                                'data-membership-request-rfi-template': '/membership/requests/123456789/rfi/',
                                'data-membership-request-ignore-template': '/membership/requests/123456789/ignore/',
                                'data-membership-request-note-add-template': '/membership/requests/123456789/notes/add/',
                                'data-membership-request-note-summary-template': '/api/v1/membership/requests/123456789/notes/summary',
                                'data-membership-request-note-detail-template': '/api/v1/membership/requests/123456789/notes',
                                'data-membership-user-profile-template': '/user/__username__/',
                                'data-membership-organization-detail-template': '/organization/123456789/',
                                'data-membership-requests-can-request-info': 'true',
                                'data-membership-requests-notes-can-view': 'true',
                                'data-membership-requests-notes-can-write': 'true',
                                'data-membership-requests-notes-can-vote': 'true',
                            }},
                        }});
                        document._root = root;

                        const pendingTableElement = registerElement({{ id: 'membership-requests-pending-table', tagName: 'table' }});
                        const pendingTbody = registerElement({{ tagName: 'tbody' }});
                        pendingTableElement.appendChild(pendingTbody);
                        document._pendingTbody = pendingTbody;
                        const pendingInfo = registerElement({{ id: 'membership-requests-pending-info', tagName: 'div' }});
                        const pendingPager = registerElement({{ id: 'membership-requests-pending-pager', tagName: 'ul' }});

                        const onHoldTableElement = registerElement({{ id: 'membership-requests-on-hold-table', tagName: 'table' }});
                        const onHoldTbody = registerElement({{ tagName: 'tbody' }});
                        onHoldTableElement.appendChild(onHoldTbody);
                        document._onHoldTbody = onHoldTbody;
                        const onHoldInfo = registerElement({{ id: 'membership-requests-on-hold-info', tagName: 'div' }});
                        const onHoldPager = registerElement({{ id: 'membership-requests-on-hold-pager', tagName: 'ul' }});

                        const pendingSelectAll = registerElement({{ id: 'select-all-requests', tagName: 'input' }});
                        const onHoldSelectAll = registerElement({{ id: 'select-all-requests-on-hold', tagName: 'input' }});
                        const pendingApply = registerElement({{ id: 'bulk-apply', tagName: 'button', disabled: true }});
                        const onHoldApply = registerElement({{ id: 'bulk-apply-on-hold', tagName: 'button', disabled: true }});
                        const filterSelect = registerElement({{ id: 'requests-filter', tagName: 'select', value: 'all' }});
                        const pendingCount = registerElement({{ id: 'membership-requests-pending-count', tagName: 'div' }});
                        const onHoldCount = registerElement({{ id: 'membership-requests-on-hold-count', tagName: 'div' }});

                        function buildForm(id, nextValue) {{
                            const form = registerElement({{ id, tagName: 'form' }});
                            const nextInput = new ElementStub(document, {{
                                tagName: 'input',
                                value: nextValue,
                                attributes: {{ name: 'next' }},
                            }});
                            const actionSelect = new ElementStub(document, {{
                                tagName: 'select',
                                value: 'accept',
                                attributes: {{ name: 'bulk_action' }},
                            }});
                            const submitButton = new ElementStub(document, {{ tagName: 'button' }});
                            form.appendChild(nextInput);
                            form.appendChild(actionSelect);
                            form.appendChild(submitButton);
                            return form;
                        }}

                        const bulkForm = buildForm('bulk-action-form', '/stale/');
                        const onHoldBulkForm = buildForm('bulk-action-form-on-hold', '/stale/');
                        const csrfInput = new ElementStub(document, {{ tagName: 'input', value: 'csrf', attributes: {{ name: 'csrfmiddlewaretoken' }} }});
                        document._csrfInput = csrfInput;

                        const approveModal = registerElement({{ id: 'shared-approve-modal', tagName: 'div', className: 'modal' }});
                        const approveModalForm = buildForm('shared-approve-form', '/stale/');
                        approveModal.appendChild(approveModalForm);

                        const window = {{
                            document,
                            CustomEvent: CustomEventStub,
                            Date,
                            URL,
                            URLSearchParams,
                            setTimeout,
                            clearTimeout,
                            location: {{
                                href: 'http://example.test/membership/requests/?filter=renewals&pending_page=2&on_hold_page=3',
                                pathname: '/membership/requests/',
                                search: '?filter=renewals&pending_page=2&on_hold_page=3',
                                assign(url) {{
                                    window._assignedUrl = url;
                                }},
                            }},
                            history: {{
                                replaceState(_state, _title, url) {{
                                    const updated = new URL(url, 'http://example.test');
                                    window.location.href = updated.toString();
                                    window.location.pathname = updated.pathname;
                                    window.location.search = updated.search;
                                }},
                            }},
                            fetchQueue: [],
                            fetchCalls: [],
                            fetch(url) {{
                                window.fetchCalls.push({{ url }});
                                if (!window.fetchQueue.length) {{
                                    if (String(url).indexOf('/notes/summary') !== -1) {{
                                        return Promise.resolve({{
                                            ok: true,
                                            json() {{
                                                return Promise.resolve({{
                                                    note_count: 0,
                                                    approvals: 0,
                                                    disapprovals: 0,
                                                    current_user_vote: '',
                                                }});
                                            }},
                                        }});
                                    }}
                                    throw new Error('Missing fetch payload for ' + url);
                                }}
                                const payload = window.fetchQueue.shift();
                                if (payload && payload.__fetchError) {{
                                    return Promise.reject(new Error(String(payload.__fetchError)));
                                }}
                                if (payload && Object.prototype.hasOwnProperty.call(payload, 'ok')) {{
                                    return Promise.resolve({{
                                        ok: !!payload.ok,
                                        json() {{
                                            return Promise.resolve(payload.payload || {{}});
                                        }},
                                    }});
                                }}
                                return Promise.resolve({{
                                    ok: true,
                                    json() {{
                                        return Promise.resolve(payload);
                                    }},
                                }});
                            }},
                            _assignedUrl: null,
                        }};

                        const tableRegistry = Object.create(null);

                        function JQueryCollection(selector) {{
                            this.selector = selector;
                            tableRegistry[selector] = tableRegistry[selector] || {{ handlers: Object.create(null), table: null }};
                        }}

                        JQueryCollection.prototype.DataTable = function (config) {{
                            const pageIndex = Math.floor((config.displayStart || 0) / config.pageLength);
                            const record = tableRegistry[this.selector];
                            record.config = config;
                            record.currentPageIndex = pageIndex;
                            record.table = {{
                                reloadCalls: [],
                                ajax: {{
                                    reload(arg1, arg2) {{
                                        record.table.reloadCalls.push([arg1, arg2]);
                                    }},
                                }},
                                page: {{
                                    drawCalls: [],
                                    set(targetPage) {{
                                        record.currentPageIndex = targetPage;
                                        return this;
                                    }},
                                    draw(mode) {{
                                        this.drawCalls.push(mode);
                                        return this;
                                    }},
                                    info() {{
                                        return {{ page: record.currentPageIndex }};
                                    }},
                                }},
                            }};
                            return record.table;
                        }};

                        JQueryCollection.prototype.on = function (eventName, handler) {{
                            const record = tableRegistry[this.selector];
                            record.handlers[eventName] = record.handlers[eventName] || [];
                            record.handlers[eventName].push(handler);
                            return this;
                        }};

                        function $(selector) {{
                            return new JQueryCollection(selector);
                        }}
                        $.fn = {{ DataTable: true }};
                        window.jQuery = $;

                        function assert(condition, message) {{
                            if (!condition) {{
                                throw new Error(message);
                            }}
                        }}

                        function dispatchDocumentEvent(type, target) {{
                            const event = {{ type, target, defaultPrevented: false, preventDefault() {{ this.defaultPrevented = true; }} }};
                            document.dispatchEvent(event);
                            return event;
                        }}

                        async function loadTable(selector, payload, draw, extraFetchPayloads) {{
                            const record = tableRegistry[selector];
                            if (payload) {{
                                window.fetchQueue.push(payload);
                            }}
                            (extraFetchPayloads || []).forEach((queuedPayload) => window.fetchQueue.push(queuedPayload));
                            await new Promise((resolve, reject) => {{
                                try {{
                                    record.config.ajax({{ draw, start: 0 }}, function (_payload) {{
                                        setTimeout(resolve, 0);
                                    }});
                                }} catch (error) {{
                                    reject(error);
                                }}
                            }});
                        }}

                        async function flushAsync() {{
                            await Promise.resolve();
                            await Promise.resolve();
                            await new Promise((resolve) => setTimeout(resolve, 0));
                            await new Promise((resolve) => setTimeout(resolve, 0));
                        }}

                        function triggerTableEvent(selector, eventName) {{
                            const record = tableRegistry[selector];
                            (record.handlers[eventName] || []).forEach((handler) => handler.call(null, {{ type: eventName }}));
                        }}

                        function pendingRow(requestId) {{
                            return {{
                                request_id: requestId,
                                status: 'pending',
                                requested_at: '2026-04-20T09:00:00',
                                target: {{ kind: 'user', label: 'alice', secondary_label: 'alice', username: 'alice', deleted: false }},
                                requested_by: {{ show: false }},
                                membership_type: {{ name: 'Individual' }},
                                is_renewal: false,
                                responses: [],
                                compact_notes: null,
                            }};
                        }}

                        function onHoldRow(requestId) {{
                            return {{
                                request_id: requestId,
                                status: 'on_hold',
                                requested_at: '2026-04-20T09:00:00',
                                on_hold_since: '2026-04-19T09:00:00',
                                target: {{ kind: 'organization', label: 'org', secondary_label: '', organization_id: 42, deleted: false }},
                                requested_by: {{ show: false }},
                                membership_type: {{ name: 'Gold Sponsor' }},
                                is_renewal: false,
                                compact_notes: null,
                            }};
                        }}

                        const context = vm.createContext({{
                            window,
                            document,
                            console,
                            Date,
                            URL,
                            URLSearchParams,
                            setTimeout,
                            clearTimeout,
                        }});
                        vm.runInContext(source, context);

                        async function runScenario() {{
                            {test_body}
                        }}

                        runScenario().catch((error) => {{
                            console.error(error && error.stack ? error.stack : String(error));
                            process.exit(1);
                        }});
                        """
                )

                result = subprocess.run(
                        ["node"],
                        input=node_script,
                        capture_output=True,
                        text=True,
                        check=False,
                        cwd=str(settings.BASE_DIR),
                )

                if result.returncode != 0:
                        message = result.stderr.strip() or result.stdout.strip() or "Node scenario failed."
                        self.fail(message)

        def test_script_executes_note_redraw_and_recomputes_bulk_and_modal_next(self) -> None:
                self._run_node_scenario(
                        """
                        assert(
                            document._pendingTbody.innerHTML.includes('Loading pending requests...'),
                            'Pending table should render a loading placeholder before the first draw completes.',
                        );
                        assert(
                            document._onHoldTbody.innerHTML.includes('Loading on-hold requests...'),
                            'On-hold table should render a loading placeholder before the first draw completes.',
                        );

                        const bulkNext = bulkForm.querySelector('input[name="next"]');
                        const modalNext = approveModalForm.querySelector('input[name="next"]');

                        dispatchDocumentEvent('submit', bulkForm);
                        dispatchDocumentEvent('submit', approveModalForm);

                        assert(
                            bulkNext.value === '/membership/requests/?filter=renewals&pending_page=2&on_hold_page=3',
                            'Bulk submit should recompute next from the canonical browser URL.',
                        );
                        assert(
                            modalNext.value === '/membership/requests/?filter=renewals&pending_page=2&on_hold_page=3',
                            'Modal submit should recompute next from the canonical browser URL.',
                        );

                        document.dispatchEvent(new window.CustomEvent('astra:membership-notes-posted', {
                            bubbles: true,
                            cancelable: true,
                            detail: { section: 'pending', requestPk: '101' },
                        }));

                        assert(
                            tableRegistry['#membership-requests-pending-table'].table.reloadCalls.length === 1,
                            'Posting a pending-row note should reload the pending table.',
                        );
                        assert(
                            tableRegistry['#membership-requests-pending-table'].table.reloadCalls[0][1] === false,
                            'Pending note redraw should preserve the current canonical page.',
                        );
                        assert(
                            tableRegistry['#membership-requests-on-hold-table'].table.reloadCalls.length === 0,
                            'Posting a pending-row note must not reload the on-hold table.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_updates_shell_counts_and_filter_labels_from_api_results(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 6,
                            recordsFiltered: 2,
                            pending_filter: {
                                selected: 'renewals',
                                options: [
                                    { value: 'all', label: 'All', count: 6 },
                                    { value: 'renewals', label: 'Renewals', count: 2 },
                                    { value: 'sponsorships', label: 'Sponsorships', count: 1 },
                                    { value: 'individuals', label: 'Individuals', count: 3 },
                                    { value: 'mirrors', label: 'Mirrors', count: 0 },
                                ],
                            },
                            data: [pendingRow(101), pendingRow(102)],
                        }, 1);

                        await loadTable('#membership-requests-on-hold-table', {
                            draw: 1,
                            recordsTotal: 4,
                            recordsFiltered: 4,
                            data: [onHoldRow(201)],
                        }, 1);

                        assert(
                            pendingCount.innerHTML === 'Pending: 2',
                            'Pending header count should be populated from the pending API response.',
                        );
                        assert(
                            onHoldCount.innerHTML === 'On hold: 4',
                            'On-hold header count should be populated from the on-hold API response.',
                        );
                        assert(
                            filterSelect.innerHTML.includes('All (6)'),
                            'Filter labels should be updated from the pending API filter metadata.',
                        );
                        assert(
                            filterSelect.innerHTML.includes('Renewals (2)'),
                            'Selected filter count should be updated from the pending API filter metadata.',
                        );
                        assert(
                            filterSelect.value === 'renewals',
                            'Client-side filter sync should preserve the currently selected filter.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_renders_ssr_style_footer_summary_and_pager_chrome(self) -> None:
                self._run_node_scenario(
                        """
                        const onHoldRows = [];
                        for (let index = 0; index < 10; index += 1) {
                            onHoldRows.push(onHoldRow(300 + index));
                        }

                        await loadTable('#membership-requests-on-hold-table', {
                            draw: 1,
                            recordsTotal: 11,
                            recordsFiltered: 11,
                            data: onHoldRows,
                        }, 1);

                        assert(
                            onHoldInfo.innerHTML === 'Showing 1–10 of 11',
                            'On-hold footer should restore the SSR-style Showing X–Y of Z summary.',
                        );
                        assert(
                            onHoldPager.innerHTML.includes('aria-label="Previous"'),
                            'Custom pager should restore the SSR previous control chrome.',
                        );
                        assert(
                            onHoldPager.innerHTML.includes('aria-label="Next"'),
                            'Custom pager should restore the SSR next control chrome.',
                        );
                        assert(
                            onHoldPager.innerHTML.includes('page-item active'),
                            'Custom pager should mark the current page with the SSR active page chrome.',
                        );
                        assert(
                            onHoldPager.innerHTML.includes('>2<'),
                            'Custom pager should render numbered page links instead of relying on DataTables default controls.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_renders_plain_on_hold_approve_button_copy(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-on-hold-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [onHoldRow(201)],
                        }, 1);

                        assert(
                            document._onHoldTbody.innerHTML.includes('>Approve</button>'),
                            'On-hold rows should render a plain Approve action trigger label.',
                        );
                        assert(
                            document._onHoldTbody.innerHTML.includes('Approve this on-hold request with committee override'),
                            'On-hold action trigger copy should still describe the committee override path.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_prunes_same_page_selection_when_rows_disappear_and_clears_on_route_changes(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', { draw: 1, recordsTotal: 1, recordsFiltered: 1, data: [pendingRow(101)] }, 1);
                        await loadTable('#membership-requests-on-hold-table', { draw: 1, recordsTotal: 1, recordsFiltered: 1, data: [onHoldRow(201)] }, 1);

                        let pendingCheckbox = document.querySelectorAll('.request-checkbox--pending')[0];
                        let onHoldCheckbox = document.querySelectorAll('.request-checkbox--on-hold')[0];
                        pendingCheckbox.checked = true;
                        onHoldCheckbox.checked = true;
                        dispatchDocumentEvent('change', pendingCheckbox);
                        dispatchDocumentEvent('change', onHoldCheckbox);

                        await loadTable('#membership-requests-pending-table', { draw: 2, recordsTotal: 1, recordsFiltered: 1, data: [pendingRow(101)] }, 2);
                        pendingCheckbox = document.querySelectorAll('.request-checkbox--pending')[0];
                        assert(pendingCheckbox.checked, 'Same-page redraw should preserve selection for rows still present.');

                        await loadTable('#membership-requests-pending-table', { draw: 3, recordsTotal: 1, recordsFiltered: 1, data: [pendingRow(102)] }, 3);
                        await loadTable('#membership-requests-pending-table', { draw: 4, recordsTotal: 1, recordsFiltered: 1, data: [pendingRow(101)] }, 4);
                        pendingCheckbox = document.querySelectorAll('.request-checkbox--pending')[0];
                        assert(
                            !pendingCheckbox.checked,
                            'Selection should be dropped once a previously selected row disappears on a same-page redraw.',
                        );

                        let refreshedOnHoldCheckbox = document.querySelectorAll('.request-checkbox--on-hold')[0];
                        refreshedOnHoldCheckbox.checked = true;
                        dispatchDocumentEvent('change', refreshedOnHoldCheckbox);

                        triggerTableEvent('#membership-requests-pending-table', 'page.dt');
                        await loadTable('#membership-requests-pending-table', { draw: 5, recordsTotal: 1, recordsFiltered: 1, data: [pendingRow(101)] }, 5);
                        await loadTable('#membership-requests-on-hold-table', { draw: 5, recordsTotal: 1, recordsFiltered: 1, data: [onHoldRow(201)] }, 5);

                        pendingCheckbox = document.querySelectorAll('.request-checkbox--pending')[0];
                        refreshedOnHoldCheckbox = document.querySelectorAll('.request-checkbox--on-hold')[0];
                        assert(!pendingCheckbox.checked, 'Pending page changes should clear pending selection.');
                        assert(!refreshedOnHoldCheckbox.checked, 'Canonical page changes should clear on-hold selection too.');
                        assert(!pendingSelectAll.checked, 'Pending select-all should be reset after a page change.');
                        assert(!onHoldSelectAll.checked, 'On-hold select-all should be reset after a page change.');

                        pendingCheckbox.checked = true;
                        refreshedOnHoldCheckbox.checked = true;
                        dispatchDocumentEvent('change', pendingCheckbox);
                        dispatchDocumentEvent('change', refreshedOnHoldCheckbox);
                        filterSelect.value = 'mirrors';
                        dispatchDocumentEvent('change', filterSelect);

                        assert(
                            window._assignedUrl === null,
                            'Filter changes should not trigger a full-page navigation.',
                        );
                        assert(
                            window.location.search === '?filter=mirrors',
                            'Filter changes should clear both section page params to restore the canonical route state.',
                        );
                        assert(
                            tableRegistry['#membership-requests-pending-table'].table.reloadCalls.length === 1,
                            'Filter changes should redraw the pending table in place.',
                        );
                        assert(
                            tableRegistry['#membership-requests-on-hold-table'].table.reloadCalls.length === 1,
                            'Filter changes should redraw the on-hold table too because canonical page state resets for both sections.',
                        );
                        assert(!pendingSelectAll.checked, 'Filter changes should clear pending bulk selection UI.');
                        assert(!onHoldSelectAll.checked, 'Filter changes should clear on-hold bulk selection UI too.');
                        assert(
                            !document.querySelectorAll('.request-checkbox--pending')[0].checked,
                            'Filter changes should clear pending row selection.',
                        );
                        assert(
                            !document.querySelectorAll('.request-checkbox--on-hold')[0].checked,
                            'Filter changes should clear on-hold row selection too.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_renders_request_row_markup_with_ssr_parity(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [{
                                request_id: 101,
                                status: 'pending',
                                requested_at: '2026-04-20T09:00:00',
                                target: { kind: 'user', label: 'Alice Example', secondary_label: 'alice', username: 'alice', deleted: false },
                                requested_by: { show: true, username: 'charlie', full_name: 'Charlie Example', deleted: false },
                                membership_type: { name: 'Individual' },
                                is_renewal: true,
                                responses: [{ question: 'Why AlmaLinux?', answer_html: '<strong>Because</strong>' }],
                                compact_notes: null,
                            }],
                        }, 1);

                        assert(
                            document._pendingTbody.innerHTML.includes('<a href="/membership/requests/101/">Request #101</a>'),
                            'Request links should be rebuilt locally from the request id instead of coming from the API payload.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('<td class="text-center align-top" style="width: 40px;">'),
                            'Checkbox column should keep the SSR 40px width and top alignment.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('<td class="text-muted text-nowrap" style="width: 1%;">'),
                            'Request column should keep the SSR text-nowrap 1% width contract.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('<td style="width: 30%;">'),
                            'Requested-for column should keep the SSR 30% width contract.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('<span class="text-muted small">(alice)</span>'),
                            'User target secondary label should keep the SSR small muted markup.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('Requested by: <a href="/user/charlie/">Charlie Example <span class="text-muted">(charlie)</span></a>'),
                            'Requested-by markup should match the SSR requester cell contract.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('<details class="mt-2" open><summary class="small text-muted">Request responses</summary>'),
                            'Response details should keep the SSR wrapper markup.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('title="Approve this request"'),
                            'Approve button title should match the SSR action markup.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('aria-label="Request for Information"'),
                            'RFI button aria-label should match the SSR action markup.',
                        );
                        assert(
                            !document._pendingTbody.innerHTML.includes('data-target="#shared-reject-modal" data-action-url="/reject/101/" data-modal-title="Reject Individual request" data-request-id="101" data-request-target="alice" data-membership-type="Individual" data-body-emphasis="alice"'),
                            'Reject button should not emit body-emphasis attributes the SSR template never renders.',
                        );
                        assert(
                            !document._pendingTbody.innerHTML.includes('data-target="#shared-rfi-modal" data-action-url="/rfi/101/" data-modal-title="Request information for Individual request" data-request-id="101" data-request-target="alice" data-membership-type="Individual" data-body-emphasis="alice"'),
                            'RFI button should not emit body-emphasis attributes the SSR template never renders.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_renders_deleted_organization_target_like_ssr_template(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [{
                                request_id: 103,
                                status: 'pending',
                                requested_at: '2026-04-20T09:00:00',
                                target: { kind: 'organization', label: 'Example Org', secondary_label: '', organization_id: null, deleted: true },
                                requested_by: { show: false },
                                membership_type: { name: 'Individual' },
                                is_renewal: false,
                                responses: [],
                                compact_notes: null,
                            }],
                        }, 1);

                        assert(
                            document._pendingTbody.innerHTML.includes('<span>Example Org</span> <span class="text-muted">(deleted)</span>'),
                            'Deleted organization targets should render the same label plus deleted marker as the SSR target template.',
                        );
                        process.exit(0);
                        """
                )

                def test_script_disables_datatables_autowidth_for_ssr_table_width_parity(self) -> None:
                    script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_requests_datatables.js"
                    source = script_path.read_text(encoding="utf-8")

                    self.assertIn("autoWidth: false", source)

        def test_script_renders_note_shell_without_embedded_queue_notes(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [{
                                request_id: 102,
                                requested_at: '2026-04-20T09:00:00',
                                target: { kind: 'user', label: 'Alice Example', secondary_label: 'alice', username: 'alice', deleted: false },
                                requested_by: { show: false },
                                membership_type: { name: 'Individual' },
                                is_renewal: false,
                                responses: [],
                                compact_notes: null,
                            }],
                        }, 1);

                        assert(
                            document._pendingTbody.innerHTML.includes('Membership Committee Notes'),
                            'Queue rows should still render the note card shell without embedding note data in the list payload.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('action="/membership/requests/102/notes/add/"'),
                            'Note form action should still be rebuilt from the page-scoped note-add template.',
                        );
                        assert(
                            !document._pendingTbody.innerHTML.includes('direct-chat-msg'),
                            'Expanded note content must not be rendered from the queue row payload before lazy detail loading runs.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_fetches_note_details_only_when_expanded_and_reuses_cached_result(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [pendingRow(104)],
                        }, 1, [{
                            note_count: 1,
                            approvals: 0,
                            disapprovals: 0,
                            current_user_vote: '',
                        }]);

                        assert(
                            window.fetchCalls.filter((call) => call.url.indexOf('/notes/summary') !== -1).length === 1,
                            'Initial row hydration should fetch note summary once.',
                        );
                        assert(
                            window.fetchCalls.filter((call) => call.url === '/api/v1/membership/requests/104/notes').length === 0,
                            'Initial row hydration must not fetch note details before expansion.',
                        );
                        assert(
                            document._pendingTbody.innerHTML.includes('Expand to load notes.'),
                            'Collapsed note shell should keep the lazy-load placeholder before expansion.',
                        );

                        const noteCard = document.getElementById('membership-notes-card-104');
                        const noteHeader = document.getElementById('membership-notes-header-104');
                        const noteContainer = document.getElementById('membership-notes-container-104');
                        const collapseBtn = noteContainer.querySelector('[data-membership-notes-collapse="104"]');

                        assert(noteCard, 'Rendered row should expose the note card element.');
                        assert(noteHeader, 'Rendered row should expose the note card header element.');
                        assert(noteContainer, 'Rendered row should expose the note container element.');
                        assert(collapseBtn, 'Rendered row should expose the note collapse button element.');

                        collapseBtn.addEventListener('click', function () {
                            if (noteCard.classList.contains('collapsed-card')) {
                                noteCard.className = noteCard.className.replace('collapsed-card', '').trim();
                            } else {
                                noteCard.className += ' collapsed-card';
                            }
                        });

                        window.fetchQueue.push({
                            groups: [{
                                username: 'reviewer',
                                display_username: 'reviewer',
                                is_self: false,
                                is_custos: false,
                                avatar_kind: 'default',
                                avatar_url: '',
                                timestamp_display: '2026-04-20 09:00',
                                entries: [{
                                    kind: 'message',
                                    rendered_html: 'Need one more check.',
                                    is_self: false,
                                    is_custos: false,
                                    bubble_style: '',
                                }],
                            }],
                        });

                        collapseBtn.dispatchEvent(new window.CustomEvent('click', { bubbles: true, cancelable: true }));
                        await flushAsync();

                        const detailFetches = window.fetchCalls.filter((call) => call.url.indexOf('/api/v1/membership/requests/104/notes') !== -1 && call.url.indexOf('/notes/summary') === -1);
                        assert(
                            detailFetches.length === 1,
                            'Expanding the note card should fetch details exactly once.',
                        );
                        assert(
                            noteContainer.querySelector('[data-membership-notes-messages="104"]').innerHTML.includes('Need one more check.'),
                            'Expanded note content should render from the lazy detail response.',
                        );

                        collapseBtn.dispatchEvent(new window.CustomEvent('click', { bubbles: true, cancelable: true }));
                        await flushAsync();

                        const detailFetchesAfterRepeat = window.fetchCalls.filter((call) => call.url.indexOf('/api/v1/membership/requests/104/notes') !== -1 && call.url.indexOf('/notes/summary') === -1);
                        assert(
                            detailFetchesAfterRepeat.length === 1,
                            'Repeating expand/collapse should reuse cached note details without a second fetch.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_fetches_note_details_on_expand_even_when_card_class_updates_after_click(self) -> None:
            self._run_node_scenario(
                """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [pendingRow(105)],
                        }, 1, [{
                            note_count: 1,
                            approvals: 0,
                            disapprovals: 0,
                            current_user_vote: '',
                        }]);

                        const noteCard = document.getElementById('membership-notes-card-105');
                        const noteContainer = document.getElementById('membership-notes-container-105');
                        const collapseBtn = noteContainer.querySelector('[data-membership-notes-collapse="105"]');

                        assert(noteCard.classList.contains('collapsed-card'), 'Note card should start collapsed.');
                        assert(collapseBtn, 'Rendered row should expose the note collapse button element.');

                        collapseBtn.addEventListener('click', function () {
                            window.setTimeout(function () {
                                noteCard.className = noteCard.className.replace('collapsed-card', '').trim();
                            }, 75);
                        });

                        window.fetchQueue.push({
                            groups: [{
                                username: 'reviewer',
                                display_username: 'reviewer',
                                is_self: false,
                                is_custos: false,
                                avatar_kind: 'default',
                                avatar_url: '',
                                timestamp_display: '2026-04-20 09:00',
                                entries: [{
                                    kind: 'message',
                                    rendered_html: 'Loaded on expand.',
                                    is_self: false,
                                    is_custos: false,
                                    bubble_style: '',
                                }],
                            }],
                        });

                        collapseBtn.dispatchEvent(new window.CustomEvent('click', { bubbles: true, cancelable: true }));
                        await new Promise((resolve) => setTimeout(resolve, 125));
                        await flushAsync();

                        let detailFetches = window.fetchCalls.filter((call) => call.url === '/api/v1/membership/requests/105/notes');
                        assert(
                            detailFetches.length === 1,
                            'Expand intent should load note details even when the card class updates after the click handler runs.',
                        );

                        collapseBtn.addEventListener('click', function () {
                            window.setTimeout(function () {
                                noteCard.className += ' collapsed-card';
                            }, 75);
                        });

                        collapseBtn.dispatchEvent(new window.CustomEvent('click', { bubbles: true, cancelable: true }));
                        await new Promise((resolve) => setTimeout(resolve, 125));
                        await flushAsync();

                        detailFetches = window.fetchCalls.filter((call) => call.url === '/api/v1/membership/requests/105/notes');
                        assert(
                            detailFetches.length === 1,
                            'Collapse intent must not trigger a detail fetch when the card class updates after the click handler runs.',
                        );
                        process.exit(0);
                        """
                )

        def test_script_preserves_request_resubmitted_diff_escaping_and_linebreaks_in_queue_notes(self) -> None:
                script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_requests_datatables.js"
                source = script_path.read_text(encoding="utf-8")

                self.assertIn(
                        "escapeHtmlWithLineBreaks(diffRow.old_value || '')",
                        source,
                )
                self.assertIn(
                        "escapeHtmlWithLineBreaks(diffRow.new_value || '')",
                        source,
                )

        def test_script_marks_note_summary_failure_as_degraded_instead_of_zero_state(self) -> None:
                self._run_node_scenario(
                        """
                        await loadTable('#membership-requests-pending-table', {
                            draw: 1,
                            recordsTotal: 1,
                            recordsFiltered: 1,
                            data: [pendingRow(107)],
                        }, 1, [{
                            ok: false,
                            payload: { error: 'summary refresh failed' },
                        }]);
                        await flushAsync();

                        const noteContainer = document.getElementById('membership-notes-container-107');
                        const countBadge = noteContainer.querySelector('[data-membership-notes-count="107"]');

                        assert(countBadge.textContent === '!', 'Queue summary failure should show a visible degraded marker instead of a fake zero count.');
                        assert(countBadge.getAttribute('title') === 'Note summary unavailable', 'Queue summary failure should expose a truthful degraded title.');
                        assert(countBadge.className.includes('badge-warning'), 'Queue summary failure should visibly mark the count badge as degraded.');
                        process.exit(0);
                        """
                )
