import csv
import hashlib
import io
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from import_export.formats import base_formats
from tablib import Dataset

from core.admin import OrganizationCSVImportLinkAdmin
from core.backends import FreeIPAUser
from core.csv_import_utils import extract_csv_headers_from_uploaded_file
from core.models import Organization, OrganizationCSVImportLink
from core.organization_csv_import import OrganizationCSVImportResource


class OrganizationCSVImportUtilitiesTests(TestCase):
    def test_extract_headers_supports_comma(self) -> None:
        uploaded = SimpleUploadedFile(
            "orgs.csv",
            "Name,Country Code,Website\nAcme,US,https://example.com\n".encode(),
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
            "Name\tCountry Code\tWebsite\nAcme\tUS\thttps://example.com\n".encode(),
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
            patch("core.backends.FreeIPAUser.get", side_effect=get_user),
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
            patch("core.backends.FreeIPAUser.get", side_effect=get_user),
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
            patch("core.backends.FreeIPAUser.get", side_effect=get_user),
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
            patch("core.backends.FreeIPAUser.get", side_effect=get_user),
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
