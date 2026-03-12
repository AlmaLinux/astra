import csv
import hashlib
import html
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from django import forms
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from import_export.formats import base_formats
from tablib import Dataset

from core.admin import OrganizationCSVImportLinkAdmin
from core.csv_import_utils import extract_csv_headers_from_uploaded_file, sanitize_csv_cell
from core.freeipa.user import FreeIPAUser
from core.models import Organization, OrganizationCSVImportLink
from core.organization_csv_import import OrganizationCSVImportResource


class OrganizationCSVImportUtilitiesTests(TestCase):
    def test_extract_headers_supports_comma(self) -> None:
        uploaded = SimpleUploadedFile(
            "orgs.csv",
            b"Name,Country Code,Website\nAcme,US,https://example.com\n",
            content_type="text/csv",
        )

        headers = extract_csv_headers_from_uploaded_file(uploaded)

        self.assertEqual(headers, ["Name", "Country Code", "Website"])

    def test_extract_headers_supports_bom_and_semicolon(self) -> None:
        uploaded = SimpleUploadedFile(
            "orgs.csv",
            "\ufeffName;Country Code;Website\nAcme;US;https://example.com\n".encode(),
            content_type="text/csv",
        )

        headers = extract_csv_headers_from_uploaded_file(uploaded)

        self.assertEqual(headers, ["Name", "Country Code", "Website"])

    def test_extract_headers_supports_tab_delimiter(self) -> None:
        uploaded = SimpleUploadedFile(
            "orgs.tsv",
            b"Name\tCountry Code\tWebsite\nAcme\tUS\thttps://example.com\n",
            content_type="text/csv",
        )

        headers = extract_csv_headers_from_uploaded_file(uploaded)

        self.assertEqual(headers, ["Name", "Country Code", "Website"])

    def test_extract_headers_supports_latin1_accents_without_corruption(self) -> None:
        uploaded = SimpleUploadedFile(
            "orgs.csv",
            "García,Country Code\nAcme,US\n".encode("latin-1"),
            content_type="text/csv",
        )

        headers = extract_csv_headers_from_uploaded_file(uploaded)

        self.assertEqual(headers, ["García", "Country Code"])

    def test_extract_headers_supports_cr_separated_rows_with_quoted_multiline_fields(self) -> None:
        uploaded = SimpleUploadedFile(
            "orgs.csv",
            b"Name,Country Code,Website\r\"Acme\rCorp\",US,https://example.com\r",
            content_type="text/csv",
        )

        headers = extract_csv_headers_from_uploaded_file(uploaded)

        self.assertEqual(headers, ["Name", "Country Code", "Website"])


class OrganizationCSVImportResourceTests(TestCase):
    def test_decision_skips_missing_required_field(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Missing required field: name")

    def test_decision_skips_duplicate_against_database_case_insensitive(self) -> None:
        Organization.objects.create(
            name="Acme Corp",
            country_code="US",
            business_contact_name="Existing",
            business_contact_email="existing-biz@example.com",
            pr_marketing_contact_name="Existing",
            pr_marketing_contact_email="existing-pr@example.com",
            technical_contact_name="Existing",
            technical_contact_email="existing-tech@example.com",
            website="https://existing.example.com",
            website_logo="https://existing.example.com/logo.png",
        )

        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "acme corp",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Organization already exists")

    def test_decision_skips_duplicate_within_csv(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz2",
                "biz2@example.com",
                "+1 555 211",
                "PR2",
                "pr2@example.com",
                "+1 555 212",
                "Tech2",
                "tech2@example.com",
                "+1 555 213",
                "https://example.org",
                "https://example.org/logo.png",
                "US",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        for row in dataset.dict:
            decision, reason = resource._decision_for_row(row)
            self.assertEqual(decision, "SKIP")
            self.assertEqual(reason, "Duplicate organization in CSV")

    def test_decision_skips_invalid_email(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "not-an-email",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Invalid email address: business_contact_email")

    def test_decision_allows_empty_pr_marketing_contact_email(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "",
                "US",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "IMPORT")
        self.assertEqual(reason, "Ready to import")

    def test_decision_allows_empty_optional_phone_fields(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "",
                "PR",
                "",
                "",
                "Tech",
                "",
                "",
                "https://example.com",
                "",
                "US",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "IMPORT")
        self.assertEqual(reason, "Ready to import")

    def test_decision_skips_invalid_country_code(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "USA",
            ]
        )

        resource = OrganizationCSVImportResource(actor_username="alex")
        resource.before_import(dataset)

        decision, reason = resource._decision_for_row(dataset.dict[0])

        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Invalid country code")

    def test_representative_hint_precedence_prefers_username_over_email(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
                "representative_username",
                "representative_email",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
                "alice",
                "bob@example.com",
            ]
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "mail": ["bob@example.com"]})

        with (
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[alice, bob]),
            patch("core.organization_csv_import.FreeIPAUser.get", return_value=alice),
        ):
            resource = OrganizationCSVImportResource(actor_username="alex")
            resource.before_import(dataset)
            usernames = resource._suggested_usernames_for_row(dataset.dict[0])

        self.assertEqual(usernames, ["alice"])

    def test_suggestions_fallback_to_contact_emails_when_no_representative_hints(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
                "representative_username",
                "representative_email",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
                "",
                "",
            ]
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["biz@example.com"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "mail": ["tech@example.com"]})

        with (
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[alice, bob]),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=[]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
        ):
            resource = OrganizationCSVImportResource(actor_username="alex")
            resource.before_import(dataset)
            usernames = resource._suggested_usernames_for_row(dataset.dict[0])

        self.assertEqual(usernames, ["alice", "bob"])

    def test_suggestions_accept_representative_username_hint_even_if_not_in_cached_list(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "pr_marketing_contact_name",
                "pr_marketing_contact_email",
                "pr_marketing_contact_phone",
                "technical_contact_name",
                "technical_contact_email",
                "technical_contact_phone",
                "website",
                "website_logo",
                "country_code",
                "representative_username",
                "representative_email",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://example.com",
                "https://example.com/logo.png",
                "US",
                "Alice",
                "",
            ]
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})

        with (
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[]),
            patch("core.organization_csv_import.FreeIPAUser.get", return_value=alice),
        ):
            resource = OrganizationCSVImportResource(actor_username="alex")
            resource.before_import(dataset)
            usernames = resource._suggested_usernames_for_row(dataset.dict[0])

        self.assertEqual(usernames, ["alice"])

    def test_organization_kwargs_uses_full_address_when_split_fields_missing(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "website",
                "country_code",
                "full_address",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "https://example.com",
                "",
                "123 Main St, Austin, TX 78701, US",
            ]
        )

        with patch(
            "core.organization_csv_import.decompose_full_address_with_photon",
            return_value={
                "street": "123 Main St",
                "city": "Austin",
                "state": "Texas",
                "postal_code": "78701",
                "country_code": "US",
            },
        ):
            resource = OrganizationCSVImportResource(actor_username="alex")
            resource.before_import(dataset)
            kwargs_by_field = resource._organization_kwargs_for_row(dataset.dict[0])

        self.assertEqual(kwargs_by_field["street"], "123 Main St")
        self.assertEqual(kwargs_by_field["city"], "Austin")
        self.assertEqual(kwargs_by_field["state"], "Texas")
        self.assertEqual(kwargs_by_field["postal_code"], "78701")
        self.assertEqual(kwargs_by_field["country_code"], "US")

    def test_organization_kwargs_prefers_split_address_fields_over_full_address(self) -> None:
        dataset = Dataset(
            headers=[
                "name",
                "business_contact_name",
                "business_contact_email",
                "business_contact_phone",
                "website",
                "country_code",
                "street",
                "city",
                "state",
                "postal_code",
                "full_address",
            ]
        )
        dataset.append(
            [
                "Acme",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "https://example.com",
                "US",
                "Split Street",
                "Split City",
                "Split State",
                "99999",
                "123 Main St, Austin, TX 78701, US",
            ]
        )

        with patch(
            "core.organization_csv_import.decompose_full_address_with_photon",
            return_value={
                "street": "Geo Street",
                "city": "Geo City",
                "state": "Geo State",
                "postal_code": "00000",
                "country_code": "DE",
            },
        ):
            resource = OrganizationCSVImportResource(actor_username="alex")
            resource.before_import(dataset)
            kwargs_by_field = resource._organization_kwargs_for_row(dataset.dict[0])

        self.assertEqual(kwargs_by_field["street"], "Split Street")
        self.assertEqual(kwargs_by_field["city"], "Split City")
        self.assertEqual(kwargs_by_field["state"], "Split State")
        self.assertEqual(kwargs_by_field["postal_code"], "99999")
        self.assertEqual(kwargs_by_field["country_code"], "US")


class OrganizationCSVImportAdminFlowTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _csv_upload(self, rows: list[list[str]]) -> SimpleUploadedFile:
        headers = [
            "name",
            "business_contact_name",
            "business_contact_email",
            "business_contact_phone",
            "pr_marketing_contact_name",
            "pr_marketing_contact_email",
            "pr_marketing_contact_phone",
            "technical_contact_name",
            "technical_contact_email",
            "technical_contact_phone",
            "website",
            "website_logo",
            "country_code",
            "representative_username",
            "representative_email",
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        return SimpleUploadedFile("organizations.csv", buffer.getvalue().encode("utf-8"), content_type="text/csv")

    def test_admin_import_formats_csv_only(self) -> None:
        site = AdminSite()
        admin_instance = OrganizationCSVImportLinkAdmin(OrganizationCSVImportLink, site)

        formats = admin_instance.get_import_formats()

        self.assertEqual(formats, [base_formats.CSV])

    def test_get_import_resource_kwargs_uses_session_username_and_preserves_existing_kwargs(self) -> None:
        admin_instance = OrganizationCSVImportLinkAdmin(OrganizationCSVImportLink, AdminSite())
        request = RequestFactory().post(
            "/admin/core/organizationcsvimportlink/process_import/",
            data={
                "representative_for_deadbeef": "alice",
            },
        )
        request.session = {"_freeipa_username": "session-alex"}
        request.user = SimpleNamespace(get_username=lambda: "user-alex")

        import_form = SimpleNamespace(
            cleaned_data={
                "name_column": "Organization Name",
                "country_code_column": "Country Code",
            }
        )

        kwargs = admin_instance.get_import_resource_kwargs(request, form=import_form)

        self.assertEqual(kwargs["actor_username"], "session-alex")
        self.assertEqual(kwargs["name_column"], "Organization Name")
        self.assertEqual(kwargs["country_code_column"], "Country Code")
        self.assertEqual(kwargs["representative_selections"], {"deadbeef": "alice"})

    def test_import_action_falls_back_to_result_rows_when_valid_rows_missing(self) -> None:
        admin_instance = OrganizationCSVImportLinkAdmin(OrganizationCSVImportLink, AdminSite())
        request = RequestFactory().get("/admin/core/organizationcsvimportlink/import/")
        request.user = SimpleNamespace(is_active=True, is_staff=True, get_username=lambda: "alex")

        class DummyRowResult:
            def __init__(self, import_type: str, number: int) -> None:
                self.import_type = import_type
                self.number = number
                self.instance = SimpleNamespace()

        confirm_form = forms.Form()
        dummy_result = SimpleNamespace(rows=[DummyRowResult("new", 2), DummyRowResult("skip", 3)])

        def _import_action(_: object, __: object, *args: object, **kwargs: object) -> TemplateResponse:
            return TemplateResponse(request, "admin/import_export/import.html", {"result": dummy_result, "confirm_form": confirm_form})

        with patch("import_export.admin.ImportMixin.import_action", _import_action):
            resp = admin_instance.import_action(request)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context_data["preview_summary"], {"total": 2, "to_import": 1, "skipped": 1})
        self.assertEqual(resp.context_data["all_match_row_numbers_csv"], "2")
        self.assertEqual(confirm_form.initial["selected_row_numbers"], "2")

    def test_upload_preview_and_confirm_auto_selects_identified_representative(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        representative = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})

        uploaded = self._csv_upload(
            [
                [
                    "Acme",
                    "Biz",
                    "biz@example.com",
                    "+1 555 111",
                    "PR",
                    "pr@example.com",
                    "+1 555 112",
                    "Tech",
                    "tech@example.com",
                    "+1 555 113",
                    "https://example.com",
                    "https://example.com/logo.png",
                    "US",
                    "alice",
                    "",
                ]
            ]
        )

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return representative
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, representative]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=[]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
        ):
            import_url = reverse("admin:core_organizationcsvimportlink_import")
            preview = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview.status_code, 200)
            self.assertContains(preview, "Preview Import")
            self.assertContains(preview, "Organizations to Import")
            self.assertContains(preview, "Acme")
            self.assertContains(preview, "biz@example.com")
            self.assertContains(preview, "Unclaimed")
            row_key = hashlib.sha256(
                "|".join(
                    [
                        "Acme",
                        "US",
                        "biz@example.com",
                        "tech@example.com",
                    ]
                ).encode("utf-8")
            ).hexdigest()[:16]
            self.assertContains(preview, f"representative_for_{row_key}")
            confirm_form = preview.context["confirm_form"]

            process_url = reverse("admin:core_organizationcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            result = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(result.status_code, 302)
        organization = Organization.objects.get(name="Acme")
        self.assertEqual(organization.representative, "alice")
        self.assertEqual(organization.status, Organization.Status.active)

    def test_org_import_page_context_includes_column_fallback_norms(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        uploaded = self._csv_upload(
            [
                [
                    "Acme",
                    "Biz",
                    "biz@example.com",
                    "+1 555 111",
                    "PR",
                    "pr@example.com",
                    "+1 555 112",
                    "Tech",
                    "tech@example.com",
                    "+1 555 113",
                    "https://example.com",
                    "https://example.com/logo.png",
                    "US",
                    "",
                    "",
                ]
            ]
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user]),
            patch("core.organization_csv_import.FreeIPAUser.get", return_value=admin_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=[]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
        ):
            preview = self.client.post(
                reverse("admin:core_organizationcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        self.assertIn("csv_column_fallback_norms_json", preview.context)

        fallback_norms = json.loads(preview.context["csv_column_fallback_norms_json"])
        self.assertIn("name", fallback_norms)
        self.assertIn("organizationname", fallback_norms["name"])

    def test_org_import_page_hides_column_selectors_until_file_selected(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.get(reverse("admin:core_organizationcsvimportlink_import"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js-column-selector-row")
        self.assertContains(response, 'style="display: none;"')
        self.assertContains(response, "window.csvImportHeaders || {}")

    def test_org_import_page_uses_null_safe_fallback_norms_lookup(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.get(reverse("admin:core_organizationcsvimportlink_import"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "const fallbackNormsElement = document.getElementById(\"csv-column-fallback-norms\")")
        self.assertNotContains(response, "document.getElementById(\"csv-column-fallback-norms\").textContent")

    def test_org_import_page_renders_fallback_norms_as_raw_json(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.get(reverse("admin:core_organizationcsvimportlink_import"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="csv-column-fallback-norms"')
        self.assertNotContains(response, "{&quot;")

    def test_confirm_applies_selected_representative(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        representative = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})

        uploaded = self._csv_upload(
            [
                [
                    "Globex",
                    "Biz",
                    "biz@example.com",
                    "+1 555 111",
                    "PR",
                    "pr@example.com",
                    "+1 555 112",
                    "Tech",
                    "tech@example.com",
                    "+1 555 113",
                    "https://globex.example.com",
                    "https://globex.example.com/logo.png",
                    "US",
                    "alice",
                    "",
                ]
            ]
        )

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return representative
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, representative]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=[]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
        ):
            import_url = reverse("admin:core_organizationcsvimportlink_import")
            preview = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview.status_code, 200)
            confirm_form = preview.context["confirm_form"]
            row_key = hashlib.sha256(
                "|".join(
                    [
                        "Globex",
                        "US",
                        "biz@example.com",
                        "tech@example.com",
                    ]
                ).encode("utf-8")
            ).hexdigest()[:16]

            process_url = reverse("admin:core_organizationcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            confirm_data[f"representative_for_{row_key}"] = "alice"
            result = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(result.status_code, 302)
        organization = Organization.objects.get(name="Globex")
        self.assertEqual(organization.representative, "alice")
        self.assertEqual(organization.status, Organization.Status.active)

    @override_settings(PUBLIC_BASE_URL="https://astra.example.org")
    def test_confirm_success_surfaces_enriched_csv_download_with_imported_rows_in_original_order(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "mail": ["bob@example.com"]})

        uploaded_rows = [
            [
                "Initech",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://initech.example.com",
                "https://initech.example.com/logo.png",
                "US",
                "",
                "rep@example.com",
            ],
            [
                "Umbrella",
                "Biz",
                "umbrella-biz@example.com",
                "+1 555 211",
                "PR",
                "umbrella-pr@example.com",
                "+1 555 212",
                "Tech",
                "umbrella-tech@example.com",
                "+1 555 213",
                "",
                "https://umbrella.example.com/logo.png",
                "US",
                "",
                "",
            ],
        ]
        uploaded = self._csv_upload(uploaded_rows)

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        row_key = hashlib.sha256(
            "|".join(
                [
                    "Initech",
                    "US",
                    "biz@example.com",
                    "tech@example.com",
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]
        download_url = reverse(
            "admin:core_organizationcsvimportlink_download_enriched",
            kwargs={"token": "enriched-token"},
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, alice, bob]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=["alice", "bob"]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
            patch("core.csv_import_utils.secrets.token_urlsafe", return_value="enriched-token"),
        ):
            preview = self.client.post(
                reverse("admin:core_organizationcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview.status_code, 200)
            confirm_form = preview.context["confirm_form"]

            confirm_data = dict(confirm_form.initial)
            confirm_data[f"representative_for_{row_key}"] = "bob"
            result = self.client.post(
                reverse("admin:core_organizationcsvimportlink_process_import"),
                data=confirm_data,
                follow=True,
            )

            download_response = self.client.get(download_url)

        self.assertEqual(result.status_code, 200)
        page_content = html.unescape(result.content.decode("utf-8"))
        self.assertIn(
            "Import complete. Download the enriched CSV to review the selected representative and the new organization's Astra profile link.",
            page_content,
        )
        self.assertContains(result, "Download enriched CSV")
        self.assertContains(result, download_url)
        self.assertNotContains(result, "Import finished:")

        organization = Organization.objects.get(name="Initech")
        self.assertEqual(organization.representative, "bob")

        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response["Content-Type"], "text/csv; charset=utf-8")

        exported_rows = list(csv.reader(io.StringIO(download_response.content.decode("utf-8"))))
        self.assertEqual(
            exported_rows,
            [
                [
                    "name",
                    "business_contact_name",
                    "business_contact_email",
                    "business_contact_phone",
                    "pr_marketing_contact_name",
                    "pr_marketing_contact_email",
                    "pr_marketing_contact_phone",
                    "technical_contact_name",
                    "technical_contact_email",
                    "technical_contact_phone",
                    "website",
                    "website_logo",
                    "country_code",
                    "representative_username",
                    "representative_email",
                    "selected_org_representative",
                    "astra_organization_profile_url",
                ],
                [
                    *[sanitize_csv_cell(value) for value in uploaded_rows[0]],
                    "bob",
                    f"https://astra.example.org/organization/{organization.pk}/",
                ],
                [
                    *[sanitize_csv_cell(value) for value in uploaded_rows[1]],
                    "",
                    "",
                ],
            ],
        )

    @override_settings(PUBLIC_BASE_URL="https://astra.example.org")
    def test_confirm_success_enriched_csv_sanitizes_formula_prefixed_cells(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        representative = FreeIPAUser("bob", {"uid": ["bob"], "mail": ["bob@example.com"]})

        uploaded_rows = [
            [
                "Initech",
                "Biz",
                "biz@example.com",
                "+1 555 111",
                "PR",
                "pr@example.com",
                "+1 555 112",
                "Tech",
                "tech@example.com",
                "+1 555 113",
                "https://initech.example.com",
                "https://initech.example.com/logo.png",
                "US",
                "",
                "rep@example.com",
            ],
            [
                "=SUM(1,2)",
                "Biz",
                "unsafe@example.com",
                "+1 555 211",
                "PR",
                "unsafe-pr@example.com",
                "+1 555 212",
                "Tech",
                "unsafe-tech@example.com",
                "+1 555 213",
                "",
                "https://unsafe.example.com/logo.png",
                "US",
                "",
                "",
            ],
        ]
        uploaded = self._csv_upload(uploaded_rows)

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "bob":
                return representative
            return None

        row_key = hashlib.sha256(
            "|".join(
                [
                    "Initech",
                    "US",
                    "biz@example.com",
                    "tech@example.com",
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]
        download_url = reverse(
            "admin:core_organizationcsvimportlink_download_enriched",
            kwargs={"token": "sanitized-token"},
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, representative]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=["bob"]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
            patch("core.csv_import_utils.secrets.token_urlsafe", return_value="sanitized-token"),
        ):
            preview = self.client.post(
                reverse("admin:core_organizationcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview.status_code, 200)
            confirm_form = preview.context["confirm_form"]

            confirm_data = dict(confirm_form.initial)
            confirm_data[f"representative_for_{row_key}"] = "bob"
            self.client.post(
                reverse("admin:core_organizationcsvimportlink_process_import"),
                data=confirm_data,
                follow=True,
            )

            download_response = self.client.get(download_url)

        self.assertEqual(download_response.status_code, 200)
        exported_rows = list(csv.reader(io.StringIO(download_response.content.decode("utf-8"))))
        self.assertEqual(exported_rows[2][0], "'=SUM(1,2)")

    def test_download_enriched_route_returns_404_for_unknown_token(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.get(
                reverse(
                    "admin:core_organizationcsvimportlink_download_enriched",
                    kwargs={"token": "missing-token"},
                )
            )

        self.assertEqual(response.status_code, 404)

    @override_settings(PUBLIC_BASE_URL="https://astra.example.org")
    def test_download_enriched_uses_session_fallback_when_cache_misses(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        representative = FreeIPAUser("bob", {"uid": ["bob"], "mail": ["bob@example.com"]})

        uploaded = self._csv_upload(
            [
                [
                    "Initech",
                    "Biz",
                    "biz@example.com",
                    "+1 555 111",
                    "PR",
                    "pr@example.com",
                    "+1 555 112",
                    "Tech",
                    "tech@example.com",
                    "+1 555 113",
                    "https://initech.example.com",
                    "https://initech.example.com/logo.png",
                    "US",
                    "",
                    "rep@example.com",
                ]
            ]
        )

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "bob":
                return representative
            return None

        row_key = hashlib.sha256(
            "|".join(
                [
                    "Initech",
                    "US",
                    "biz@example.com",
                    "tech@example.com",
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]
        download_url = reverse(
            "admin:core_organizationcsvimportlink_download_enriched",
            kwargs={"token": "session-token"},
        )
        session_key = "organization-import-enriched:session-token"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, representative]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=["bob"]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
            patch("core.csv_import_utils.secrets.token_urlsafe", return_value="session-token"),
        ):
            preview = self.client.post(
                reverse("admin:core_organizationcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview.status_code, 200)
            confirm_form = preview.context["confirm_form"]

            confirm_data = dict(confirm_form.initial)
            confirm_data[f"representative_for_{row_key}"] = "bob"
            self.client.post(
                reverse("admin:core_organizationcsvimportlink_process_import"),
                data=confirm_data,
                follow=True,
            )

        session = self.client.session
        self.assertIn(session_key, session)

        with (
            patch("core.admin.cache.get", return_value=None),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
        ):
            response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(session_key, self.client.session)

        with (
            patch("core.admin.cache.get", return_value=None),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
        ):
            expired_response = self.client.get(download_url)

        self.assertEqual(expired_response.status_code, 404)

    def test_upload_preview_shows_email_hint_representative_suggestions(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        representative = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})

        uploaded = self._csv_upload(
            [
                [
                    "Initech",
                    "Biz",
                    "biz@example.com",
                    "+1 555 111",
                    "PR",
                    "pr@example.com",
                    "+1 555 112",
                    "Tech",
                    "tech@example.com",
                    "+1 555 113",
                    "https://initech.example.com",
                    "https://initech.example.com/logo.png",
                    "US",
                    "",
                    "rep@example.com",
                ]
            ]
        )

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return representative
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, representative]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=["alice", "bob"]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
        ):
            import_url = reverse("admin:core_organizationcsvimportlink_import")
            preview = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Unclaimed")
        self.assertContains(preview, "alice")
        self.assertContains(preview, "bob")
        content = preview.content.decode("utf-8")
        self.assertLess(content.find("<option value=\"alice\" selected>"), content.find(">Unclaimed<"))

    def test_upload_preview_and_confirm_skips_missing_required_fields(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "mail": ["alex@example.com"], "memberof_group": ["admins"]})
        representative = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"]})

        uploaded = self._csv_upload(
            [
                [
                    "Umbrella",
                    "Biz",
                    "biz@example.com",
                    "+1 555 111",
                    "PR",
                    "pr@example.com",
                    "+1 555 112",
                    "Tech",
                    "tech@example.com",
                    "+1 555 113",
                    "",
                    "https://umbrella.example.com/logo.png",
                    "US",
                    "alice",
                    "",
                ]
            ]
        )

        def get_user(username: str) -> FreeIPAUser | None:
            if username == "alex":
                return admin_user
            if username == "alice":
                return representative
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.all", return_value=[admin_user, representative]),
            patch("core.organization_csv_import.FreeIPAUser.get", side_effect=get_user),
            patch("core.organization_csv_import.FreeIPAUser.find_usernames_by_email", return_value=[]),
            patch("core.organization_csv_import.FreeIPAUser.find_by_email", return_value=None),
        ):
            import_url = reverse("admin:core_organizationcsvimportlink_import")
            preview = self.client.post(
                import_url,
                data={
                    "resource": "0",
                    "format": "0",
                    "import_file": uploaded,
                },
                follow=False,
            )

            self.assertEqual(preview.status_code, 200)
            self.assertContains(preview, "Skipped")
            self.assertContains(preview, "Missing required field: website")
            confirm_form = preview.context["confirm_form"]

            process_url = reverse("admin:core_organizationcsvimportlink_process_import")
            confirm_data = dict(confirm_form.initial)
            result = self.client.post(process_url, data=confirm_data, follow=False)

        self.assertEqual(result.status_code, 302)
        self.assertFalse(Organization.objects.filter(name="Umbrella").exists())


class OrganizationCSVImportAdminVisibilityTests(TestCase):
    def test_changelist_redirects_to_import_view(self) -> None:
        site = AdminSite()
        admin_instance = OrganizationCSVImportLinkAdmin(OrganizationCSVImportLink, site)
        request = RequestFactory().get("/admin/core/organizationcsvimportlink/")
        request.user = type("U", (), {"is_active": True, "is_staff": True})()

        response = admin_instance.changelist_view(request)

        self.assertEqual(response.status_code, 302)
