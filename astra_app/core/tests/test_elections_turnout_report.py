import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import Election, FreeIPAPermissionGrant, VotingCredential
from core.permissions import ASTRA_ADD_ELECTION


class ElectionsTurnoutReportTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _create_election(self, *, name: str, status: str) -> Election:
        now = timezone.now()
        start_datetime = now - datetime.timedelta(days=2)
        end_datetime = now + datetime.timedelta(days=2)
        if status == Election.Status.closed:
            end_datetime = now - datetime.timedelta(days=1)
        if status == Election.Status.tallied:
            end_datetime = now - datetime.timedelta(days=2)
        if status == Election.Status.draft:
            start_datetime = now + datetime.timedelta(days=2)
            end_datetime = now + datetime.timedelta(days=4)

        return Election.objects.create(
            name=name,
            description="",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            number_of_seats=2,
            status=status,
        )

    def test_turnout_report_blocks_non_managers(self) -> None:
        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections-turnout-report"))

        self.assertEqual(resp.status_code, 403)

    def test_turnout_report_page_is_vue_shell_without_report_builder(self) -> None:
        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.reporting._build_elections_turnout_report") as build_report,
        ):
            resp = self.client.get(reverse("elections-turnout-report"))

        self.assertEqual(resp.status_code, 200)
        build_report.assert_not_called()
        self.assertContains(resp, 'data-elections-turnout-report-root')
        self.assertContains(resp, reverse("api-elections-turnout-report-detail"))
        self.assertContains(resp, 'data-elections-turnout-report-election-detail-url-template')

    def test_turnout_report_excludes_draft_elections(self) -> None:
        draft = self._create_election(name="Draft election", status=Election.Status.draft)
        open_election = self._create_election(name="Open election", status=Election.Status.open)
        VotingCredential.objects.create(
            election=open_election,
            public_id="cred-open-1",
            freeipa_username="voter1",
            weight=1,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.get(reverse("elections-turnout-report"))
            api_resp = self.client.get(reverse("api-elections-turnout-report"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-turnout-report-root')
        self.assertContains(resp, reverse("api-elections-turnout-report"))
        self.assertNotContains(resp, open_election.name)
        self.assertNotContains(resp, draft.name)

        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual([row["election"]["name"] for row in api_resp.json()["rows"]], [open_election.name])
        self.assertNotIn("detail_url", api_resp.json()["rows"][0]["election"])

    def test_turnout_report_marks_elections_without_credentials(self) -> None:
        election = self._create_election(name="Closed without credentials", status=Election.Status.closed)

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.get(reverse("elections-turnout-report"))
            api_resp = self.client.get(reverse("api-elections-turnout-report"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-turnout-report-root')
        self.assertNotContains(resp, election.name)
        self.assertNotContains(resp, "credentials not yet issued")

        self.assertEqual(api_resp.status_code, 200)
        row = next(item for item in api_resp.json()["rows"] if item["election"]["id"] == election.id)
        self.assertNotIn("detail_url", row["election"])
        self.assertEqual(row["eligible_count"], 0)
        self.assertFalse(row["credentials_issued"])

    def test_turnout_report_includes_weight_metrics_columns_and_row_fields(self) -> None:
        election = self._create_election(name="Weighted election", status=Election.Status.open)
        VotingCredential.objects.create(
            election=election,
            public_id="cred-weighted-1",
            freeipa_username="weighted-voter",
            weight=3,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")
        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            resp = self.client.get(reverse("elections-turnout-report"))
            api_resp = self.client.get(reverse("api-elections-turnout-report"), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-elections-turnout-report-root')
        self.assertNotContains(resp, "Eligible weight")
        self.assertNotContains(resp, "Participating weight")

        self.assertEqual(api_resp.status_code, 200)
        row = next(item for item in api_resp.json()["rows"] if item["election"]["id"] == election.id)
        self.assertNotIn("detail_url", row["election"])
        self.assertEqual(row["eligible_weight"], 3)
        self.assertEqual(row["participating_weight"], 0)
        self.assertIn("turnout_count_pct", row)
        self.assertIn("turnout_weight_pct", row)
