
import csv
import datetime
import io
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

from django import forms
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from import_export.formats import base_formats
from tablib import Dataset

from core.admin import MembershipCSVImportLinkAdmin
from core.backends import FreeIPAUser
from core.membership import get_valid_memberships_for_username
from core.membership_csv_import import MembershipCSVImportForm, MembershipCSVImportResource
from core.models import Membership, MembershipCSVImportLink, MembershipLog, MembershipRequest, MembershipType, Note


class AdminImportMembershipsCSVTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_dry_run_does_not_apply_changes(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type,Committee Notes,Why?\n"
            b"Alice,alice@example.org,Active Member,2024-01-02,individual,Imported note,Because\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.post(
                url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MembershipLog.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(MembershipRequest.objects.count(), 0)
        self.assertEqual(Note.objects.count(), 0)

    def test_import_page_loads_on_get(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=admin_user):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)

    def test_import_form_membership_type_orders_by_category_then_sort(self) -> None:
        from core.models import MembershipTypeCategory

        MembershipTypeCategory.objects.filter(pk="mirror").update(is_organization=True, sort_order=1)
        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=2)

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        form = MembershipCSVImportForm(
            formats=[base_formats.CSV],
            resources=[MembershipCSVImportResource],
        )
        codes = list(form.fields["membership_type"].queryset.values_list("code", flat=True))
        self.assertLess(codes.index("mirror"), codes.index("silver"))
        self.assertLess(codes.index("silver"), codes.index("gold"))

    def test_import_formats_csv_only(self) -> None:
        site = AdminSite()
        admin_instance = MembershipCSVImportLinkAdmin(MembershipCSVImportLink, site)
        formats = admin_instance.get_import_formats()
        self.assertEqual(formats, [base_formats.CSV])

    def test_import_page_includes_format_field(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=admin_user):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="format"')

    def test_live_import_creates_membership_and_unmatched_export(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type,Committee Notes,Why?\n"
            b"Alice,alice@example.org,Active Member,2024-01-02,individual,Imported note,Because\n"
            b"Bob,bob@example.org,Active Member,2024-01-02,individual,,\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                    # Map the membership question to a specific CSV column.
                    "q_contributions_column": "Why?",
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            # Unmatched export should be available already during the preview step.
            download_url = preview_resp.context.get("unmatched_download_url")
            self.assertTrue(download_url)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)

        # Membership created via log re-save using the provided start date.
        membership = Membership.objects.get(target_username="alice", membership_type_id="individual")
        expected_start = datetime.datetime(2024, 1, 2, 0, 0, 0, tzinfo=datetime.UTC)
        self.assertEqual(membership.created_at, expected_start)

        # Request responses captured.
        req = MembershipRequest.objects.get(requested_username="alice", membership_type_id="individual")
        self.assertEqual(req.responses, [{"Contributions": "Because"}])

        # Committee note stored on the membership request.
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="alex",
                content="[Import] Imported note",
            ).exists()
        )

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            download_resp = self.client.get(download_url)
        self.assertEqual(download_resp.status_code, 200)

        decoded = download_resp.content.decode("utf-8")
        self.assertIn("bob@example.org", decoded)

        reader = csv.DictReader(io.StringIO(decoded))
        self.assertEqual(
            reader.fieldnames,
            [
                "Name",
                "Email",
                "Active Member",
                "Membership Start Date",
                "Membership Type",
                "Committee Notes",
                "Why?",
                "reason",
            ],
        )

        rows = list(reader)
        self.assertEqual(len(rows), 1)

    def test_preview_includes_stats_and_grouped_paginated_rows(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type\n"
            b"Alice,ALICE@EXAMPLE.ORG,Active Member,2024-01-02,individual\n"
            b"Bob,bob@example.org,Active Member,2024-01-02,individual\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["Alice@Example.ORG"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.post(
                url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)

        csv_stats = resp.context.get("csv_stats")
        self.assertIsInstance(csv_stats, dict)
        assert isinstance(csv_stats, dict)
        self.assertEqual(csv_stats.get("total_records"), 2)
        self.assertEqual(csv_stats.get("matched_by_email"), 1)
        self.assertEqual(csv_stats.get("matched_total"), 1)
        self.assertEqual(csv_stats.get("matched_total_percent"), 50.0)

        matches_page_obj = resp.context.get("matches_page_obj")
        skipped_page_obj = resp.context.get("skipped_page_obj")
        self.assertIsNotNone(matches_page_obj)
        self.assertIsNotNone(skipped_page_obj)
        self.assertGreaterEqual(matches_page_obj.paginator.per_page, 50)
        self.assertGreaterEqual(skipped_page_obj.paginator.per_page, 50)

    def test_unmatched_download_falls_back_to_session_cache(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type\n"
            b"Alice,alice@example.org,Active Member,2024-01-02,individual\n"
            b"Bob,bob@example.org,Active Member,2024-01-02,individual\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.post(
                url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        download_url = resp.context.get("unmatched_download_url")
        self.assertTrue(download_url)

        parsed = urlparse(download_url)
        token = parsed.path.rstrip("/").split("/")[-1]
        cache_key = f"membership-import-unmatched:{token}"
        session = self.client.session
        self.assertIn(cache_key, session)

        with (
            patch("core.admin.cache.get", return_value=None),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            download_resp = self.client.get(download_url)

        self.assertEqual(download_resp.status_code, 200)
        self.assertIn("bob@example.org", download_resp.content.decode("utf-8"))

    def test_import_can_select_subset_of_matches(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type\n"
            b"Alice,alice@example.org,Active Member,2024-01-02,individual\n"
            b"Bob,bob@example.org,Active Member,2024-01-02,individual\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )
        bob_user = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            if username == "bob":
                return bob_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user, bob_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            selected = str(confirm_form.initial.get("selected_row_numbers") or "").strip()
            self.assertTrue(selected)
            row_numbers = [p for p in selected.split(",") if p.strip()]
            self.assertGreaterEqual(len(row_numbers), 2)

            # Import only the first matched row.
            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            confirm_data["selected_row_numbers"] = row_numbers[0]
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())
        self.assertFalse(Membership.objects.filter(target_username="bob", membership_type_id="individual").exists())

    def test_preview_can_match_by_name_when_enabled(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type\n"
            b"Bob Example,bob@unknown.example.org,Active Member,2024-01-02,individual\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        bob_user = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.org"],
                "displayname": ["Bob Example"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "bob":
                return bob_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, bob_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.post(
                url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                    "enable_name_matching": "on",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        csv_stats = resp.context.get("csv_stats")
        self.assertIsInstance(csv_stats, dict)
        assert isinstance(csv_stats, dict)
        self.assertEqual(csv_stats.get("total_records"), 1)
        self.assertEqual(csv_stats.get("matched_by_email"), 0)
        self.assertEqual(csv_stats.get("matched_by_name"), 1)
        self.assertEqual(csv_stats.get("matched_total"), 1)

        matches_page_obj = resp.context.get("matches_page_obj")
        skipped_page_obj = resp.context.get("skipped_page_obj")
        self.assertIsNotNone(matches_page_obj)
        self.assertIsNotNone(skipped_page_obj)
        self.assertEqual(matches_page_obj.paginator.count, 1)
        self.assertEqual(skipped_page_obj.paginator.count, 0)

        confirm_form = resp.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        selected = str(confirm_form.initial.get("selected_row_numbers") or "").strip()
        self.assertTrue(selected)

    def test_preview_groups_by_import_type_when_decision_missing(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Name,Email,Active Member,Membership Start Date,Membership Type\n"
            b"Alice,alice@example.org,Active Member,2024-01-02,individual\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        original_populate = MembershipCSVImportResource._populate_preview_fields

        def _populate_without_decision(resource: MembershipCSVImportResource, instance: MembershipRequest, row: Any) -> None:
            original_populate(resource, instance, row)
            if hasattr(instance, "_decision"):
                delattr(instance, "_decision")

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch.object(MembershipCSVImportResource, "_populate_preview_fields", _populate_without_decision),
        ):
            url = reverse("admin:core_membershipcsvimportlink_import")
            resp = self.client.post(
                url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)

        matches_page_obj = resp.context.get("matches_page_obj")
        skipped_page_obj = resp.context.get("skipped_page_obj")
        self.assertIsNotNone(matches_page_obj)
        self.assertIsNotNone(skipped_page_obj)
        self.assertEqual(matches_page_obj.paginator.count, 1)
        self.assertEqual(skipped_page_obj.paginator.count, 0)

    def test_preview_grouping_uses_import_type_when_decision_missing(self) -> None:
        site = AdminSite()
        admin_instance = MembershipCSVImportLinkAdmin(MembershipCSVImportLink, site)
        request = RequestFactory().get("/admin/core/membershipcsvimportlink/import/")
        request.user = SimpleNamespace(is_active=True, is_staff=True, get_username=lambda: "alex")

        class DummyRowResult:
            def __init__(self) -> None:
                self.import_type = "new"
                self.number = 1
                self.instance = SimpleNamespace()

        dummy_result = SimpleNamespace(valid_rows=lambda: [DummyRowResult()])
        confirm_form = forms.Form()

        def _import_action(_: Any, __: Any, *args: Any, **kwargs: Any) -> TemplateResponse:
            return TemplateResponse(request, "admin/import_export/import.html", {"result": dummy_result, "confirm_form": confirm_form})

        with patch("import_export.admin.ImportMixin.import_action", _import_action):
            resp = admin_instance.import_action(request)

        self.assertEqual(resp.status_code, 200)
        matches_page_obj = resp.context_data.get("matches_page_obj")
        skipped_page_obj = resp.context_data.get("skipped_page_obj")
        self.assertIsNotNone(matches_page_obj)
        self.assertIsNotNone(skipped_page_obj)
        self.assertEqual(matches_page_obj.paginator.count, 1)
        self.assertEqual(skipped_page_obj.paginator.count, 0)

    def test_preview_reason_active_membership_updates_start_date(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        now = timezone.now().astimezone(datetime.UTC)
        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=now + datetime.timedelta(days=30),
        )
        Membership.objects.filter(pk=membership.pk).update(
            created_at=datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
        )

        dataset = Dataset(headers=["Email", "Start date", "Notes"])
        dataset.append(["alice@example.org", "2024-01-02", ""])

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            resource = MembershipCSVImportResource(
                membership_type=MembershipType.objects.get(code="individual"),
                actor_username="alex",
            )
            resource.before_import(dataset)
            decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "IMPORT")
        self.assertEqual(reason, "Active membership, updating start date")

    def test_preview_reason_pending_request_will_be_accepted(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Existing": "Yes"}],
        )

        dataset = Dataset(headers=["Email", "Start date", "Notes"])
        dataset.append(["alice@example.org", "2024-01-02", ""])

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            resource = MembershipCSVImportResource(
                membership_type=MembershipType.objects.get(code="individual"),
                actor_username="alex",
            )
            resource.before_import(dataset)
            decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "IMPORT")
        self.assertEqual(reason, "Active request, will be accepted")

    def test_preview_reason_on_hold_request_is_ignored(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            responses=[{"Existing": "Yes"}],
        )

        dataset = Dataset(headers=["Email", "Start date", "Notes"])
        dataset.append(["alice@example.org", "2024-01-02", ""])

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            resource = MembershipCSVImportResource(
                membership_type=MembershipType.objects.get(code="individual"),
                actor_username="alex",
            )
            resource.before_import(dataset)
            decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Request on hold, will ignore")

    def test_preview_reason_already_up_to_date_skips(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        now = timezone.now().astimezone(datetime.UTC)
        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=now + datetime.timedelta(days=30),
        )
        Membership.objects.filter(pk=membership.pk).update(
            created_at=datetime.datetime(2024, 1, 2, 0, 0, 0, tzinfo=datetime.UTC)
        )

        dataset = Dataset(headers=["Email", "Start date", "Notes"])
        dataset.append(["alice@example.org", "2024-01-02", ""])

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            resource = MembershipCSVImportResource(
                membership_type=MembershipType.objects.get(code="individual"),
                actor_username="alex",
            )
            resource.before_import(dataset)
            decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Already up-to-date")

    def test_second_import_is_marked_up_to_date(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)

        dataset = Dataset(headers=["Email", "Start date", "Notes"])
        dataset.append(["alice@example.org", "2024-01-02", "Imported note"])

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            resource = MembershipCSVImportResource(
                membership_type=MembershipType.objects.get(code="individual"),
                actor_username="alex",
            )
            resource.before_import(dataset)
            decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Already up-to-date")

    def test_live_import_mirror_question_columns_are_used(self) -> None:
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Email,Active Member,Membership Start Date,Membership Type,Domain,Pull request,Additional info\n"
            b"alice@example.org,Active Member,2024-01-02,mirror,mirror.example.org,https://github.com/AlmaLinux/mirrors/pull/1,Some notes\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "mirror",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "mirror"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)

        req = MembershipRequest.objects.get(requested_username="alice", membership_type_id="mirror")
        self.assertEqual(
            req.responses,
            [
                {"Domain": "mirror.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/1"},
                {"Additional info": "Some notes"},
            ],
        )

    def test_live_import_without_active_member_column_imports_rows(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        # This CSV intentionally omits an Active Member column. In this format,
        # every row should be treated as eligible for import.
        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
        )
    def test_live_import_old_start_date_still_creates_valid_membership(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        # The profile view only shows unexpired memberships. If the CSV start
        # date is a "member since" value far in the past, the import must not
        # backdate the approval time used for expiry calculations.
        old_start_date = (timezone.now() - datetime.timedelta(days=800)).date().isoformat().encode("utf-8")
        csv_content = b"Email,Start date,Notes\n" + b"alice@example.org," + old_start_date + b",Imported note\n"
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())
        self.assertEqual(len(get_valid_memberships_for_username("alice")), 1)

    def test_live_import_does_not_skip_expired_membership_row(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        self._login_as_freeipa_admin("alex")

        old_start_date = (timezone.now() - datetime.timedelta(days=800)).date().isoformat().encode("utf-8")
        csv_content = b"Email,Start date,Notes\n" + b"alice@example.org," + old_start_date + b",Imported note\n"
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(get_valid_memberships_for_username("alice")), 1)

    def test_live_import_reuses_existing_pending_request_without_null_requested_at(self) -> None:
        """CSV import should be idempotent when a pending request already exists."""

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        # Existing pending request which the import should reuse.
        existing = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Existing": "Yes"}],
        )
        # Make the timestamp deterministic.
        MembershipRequest.objects.filter(pk=existing.pk).update(
            requested_at=datetime.datetime(2023, 12, 1, 0, 0, 0, tzinfo=datetime.UTC)
        )

        csv_content = (
            b"Email,Start date,Notes,Why?\n"
            b"alice@example.org,2024-01-02,Imported note,Because\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
            patch("post_office.mail.send", autospec=True) as send_mail,
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)

        # Existing pending request should be approved via normal workflow, including email.
        send_mail.assert_called()

        # Still one request, not a duplicate.
        self.assertEqual(
            MembershipRequest.objects.filter(
                requested_username="alice",
                membership_type_id="individual",
                status=MembershipRequest.Status.pending,
            ).count(),
            0,
        )

        req = MembershipRequest.objects.get(pk=existing.pk)
        expected_start = datetime.datetime(2024, 1, 2, 0, 0, 0, tzinfo=datetime.UTC)
        self.assertEqual(req.requested_at, expected_start)
        # Responses are merged.
        self.assertIn({"Existing": "Yes"}, req.responses)
        self.assertIn({"Why?": "Because"}, req.responses)

        self.assertTrue(
            Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
        )

    def test_live_import_updates_start_date_for_existing_active_membership(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        now = timezone.now().astimezone(datetime.UTC)
        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=now + datetime.timedelta(days=30),
        )
        original_expires_at = membership.expires_at
        Membership.objects.filter(pk=membership.pk).update(
            created_at=datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
        )

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_to_group,
            patch("post_office.mail.send", autospec=True) as send_mail,
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)

        membership.refresh_from_db()
        expected_start = datetime.datetime(2024, 1, 2, 0, 0, 0, tzinfo=datetime.UTC)
        self.assertEqual(membership.created_at, expected_start)
        self.assertEqual(membership.expires_at, original_expires_at)

        add_to_group.assert_called()
        send_mail.assert_not_called()

    def test_live_import_on_hold_request_adds_note_without_updating_membership(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        existing_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            responses=[{"Existing": "Yes"}],
        )
        MembershipRequest.objects.filter(pk=existing_request.pk).update(
            requested_at=datetime.datetime(2023, 12, 1, 0, 0, 0, tzinfo=datetime.UTC)
        )

        now = timezone.now().astimezone(datetime.UTC)
        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            expires_at=now + datetime.timedelta(days=30),
        )
        Membership.objects.filter(pk=membership.pk).update(
            created_at=datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
        )

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_to_group,
            patch("post_office.mail.send", autospec=True) as send_mail,
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)

        membership.refresh_from_db()
        self.assertEqual(
            membership.created_at,
            datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=datetime.UTC),
        )

        req = MembershipRequest.objects.get(pk=existing_request.pk)
        self.assertEqual(req.status, MembershipRequest.Status.on_hold)
        self.assertEqual(
            req.requested_at,
            datetime.datetime(2023, 12, 1, 0, 0, 0, tzinfo=datetime.UTC),
        )

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="alex",
                content="[Import] Imported note",
            ).exists()
        )

        add_to_group.assert_not_called()
        send_mail.assert_not_called()

    def test_live_import_falls_back_to_find_by_email_when_directory_list_missing_email(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alex.iribarren@gmail.com,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        imported_user = FreeIPAUser(
            "alexi",
            {
                "uid": ["alexi"],
                "mail": ["alex.iribarren@gmail.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alexi":
                return imported_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.FreeIPAUser.find_by_email", return_value=imported_user) as find_by_email,
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        find_by_email.assert_called_with("alex.iribarren@gmail.com")
        self.assertGreaterEqual(find_by_email.call_count, 1)
        self.assertTrue(
            Membership.objects.filter(target_username="alexi", membership_type_id="individual").exists()
        )

    def test_live_import_succeeds_if_user_already_in_freeipa_group(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        # alice is already in the target group.
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
        )

    def test_group_add_failure_does_not_set_note(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True, side_effect=RuntimeError("boom")),
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        req = MembershipRequest.objects.filter(requested_username="alice", membership_type_id="individual").first()
        if req is not None:
            self.assertFalse(
                Note.objects.filter(
                    membership_request=req,
                    username="alex",
                    content="[Import] Imported note",
                ).exists()
            )
        self.assertFalse(
            Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
        )

    def test_live_import_creates_request_and_requested_log(self) -> None:
        """CSV import should follow the same workflow as user requests.

        Specifically: create a MembershipRequest + a "requested" MembershipLog,
        then approve it (without emailing the user).
        """

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "enabled": True,
                "sort_order": 0,
            },
        )

        self._login_as_freeipa_admin("alex")

        csv_content = (
            b"Email,Start date,Notes\n"
            b"alice@example.org,2024-01-02,Imported note\n"
        )
        uploaded = SimpleUploadedFile("members.csv", csv_content, content_type="text/csv")

        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )
        alice_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice_user
            return None

        with (
            patch("core.membership_csv_import.FreeIPAUser.all", return_value=[admin_user, alice_user]),
            patch("core.membership_csv_import.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.membership_csv_import.missing_required_agreements_for_user_in_group", return_value=[]),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
            patch("post_office.mail.send", autospec=True) as send_mail,
        ):
            import_url = reverse("admin:core_membershipcsvimportlink_import")
            preview_resp = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": "individual",
                    "import_file": uploaded,
                },
                follow=False,
            )
            self.assertEqual(preview_resp.status_code, 200)
            confirm_form = preview_resp.context.get("confirm_form")
            self.assertIsNotNone(confirm_form)

            process_url = reverse("admin:core_membershipcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data["membership_type"] = "individual"
            resp = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(MembershipRequest.objects.filter(requested_username="alice", membership_type_id="individual").exists())
        self.assertTrue(
            MembershipLog.objects.filter(
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.requested,
            ).exists()
        )
        self.assertTrue(
            MembershipLog.objects.filter(
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            ).exists()
        )
        send_mail.assert_not_called()
