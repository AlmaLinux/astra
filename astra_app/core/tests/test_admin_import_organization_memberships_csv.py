import csv
import datetime
import io
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import HiddenInput, ModelChoiceField
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from import_export import resources
from import_export.formats import base_formats
from post_office.models import Email
from tablib import Dataset

import core.organization_membership_csv_import as organization_membership_csv_import
from core.admin import OrganizationMembershipCSVImportLinkAdmin
from core.csv_import_utils import resolve_column_header
from core.freeipa.user import FreeIPAUser
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    Note,
    Organization,
    OrganizationMembershipCSVImportLink,
)
from core.organization_membership_csv_import import (
    OrganizationMembershipCSVConfirmImportForm,
    OrganizationMembershipCSVImportForm,
    OrganizationMembershipCSVImportResource,
)
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class OrganizationMembershipCSVImportResourceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()
        ensure_email_templates()

    def _membership_type(self, *, code: str, category_id: str, is_organization: bool = True) -> MembershipType:
        from core.models import MembershipTypeCategory

        MembershipTypeCategory.objects.filter(pk=category_id).update(is_organization=is_organization)
        membership_type, _ = MembershipType.objects.update_or_create(
            code=code,
            defaults={
                "name": code.title(),
                "group_cn": f"almalinux-{code}",
                "category_id": category_id,
                "enabled": True,
                "sort_order": 0,
            },
        )
        return membership_type

    def test_import_form_membership_type_queryset_only_includes_org_categories(self) -> None:
        individual = self._membership_type(code="individual", category_id="individual", is_organization=False)
        sponsorship = self._membership_type(code="gold", category_id="sponsorship", is_organization=True)

        form = OrganizationMembershipCSVImportForm(
            formats=[base_formats.CSV],
            resources=[OrganizationMembershipCSVImportResource],
        )

        membership_type_field = cast(ModelChoiceField, form.fields["membership_type"])
        codes = list(membership_type_field.queryset.values_list("code", flat=True))
        self.assertIn(sponsorship.code, codes)
        self.assertNotIn(individual.code, codes)

    def test_import_form_header_extract_error_does_not_crash(self) -> None:
        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            b"organization_id,organization_name\n1,Known Org\n",
            content_type="text/csv",
        )

        with patch(
            "core.organization_membership_csv_import.extract_csv_headers_from_uploaded_file",
            side_effect=RuntimeError("boom"),
        ):
            form = OrganizationMembershipCSVImportForm(
                data={},
                files={"import_file": uploaded},
                formats=[base_formats.CSV],
                resources=[OrganizationMembershipCSVImportResource],
            )

        self.assertEqual(
            form.fields["organization_id_column"].choices,
            [("", "Auto-detect")],
        )
        self.assertEqual(
            form.fields["organization_name_column"].choices,
            [("", "Auto-detect")],
        )

    def test_import_form_includes_question_column_fields_with_preferred_norms(self) -> None:
        form = OrganizationMembershipCSVImportForm(
            formats=[base_formats.CSV],
            resources=[OrganizationMembershipCSVImportResource],
        )

        self.assertIn("q_sponsorship_details_column", form.fields)

        preferred_norms = str(
            form.fields["q_sponsorship_details_column"].widget.attrs.get("data-preferred-norms", "")
        )
        self.assertIn("sponsorshipdetails", preferred_norms)
        self.assertIn("qsponsorshipdetails", preferred_norms)

    def test_import_form_populates_question_column_choices_from_uploaded_headers(self) -> None:
        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            b"organization_id,organization_name,Sponsor answer\n1,Known Org,Yes\n",
            content_type="text/csv",
        )

        form = OrganizationMembershipCSVImportForm(
            data={},
            files={"import_file": uploaded},
            formats=[base_formats.CSV],
            resources=[OrganizationMembershipCSVImportResource],
        )

        question_choices = [value for value, _label in form.fields["q_sponsorship_details_column"].choices]
        self.assertIn("Sponsor answer", question_choices)

    def test_confirm_form_includes_hidden_question_column_fields(self) -> None:
        form = OrganizationMembershipCSVConfirmImportForm()

        self.assertIn("q_sponsorship_details_column", form.fields)
        self.assertIsInstance(form.fields["q_sponsorship_details_column"].widget, HiddenInput)

    def test_name_match_is_exact_case_insensitive_without_alnum_normalization(self) -> None:
        membership_type = self._membership_type(code="silver", category_id="sponsorship")
        Organization.objects.create(
            name="ACME, Inc.",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        dataset = Dataset(headers=["organization_name"])
        dataset.append(["acme inc"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        resource.before_import(dataset)

        decision, reason, _organization, _row_note, _responses, _start_at, _end_at = resource._decision_for_row(dataset.dict[0])
        self.assertEqual(decision, "SKIP")
        self.assertEqual(reason, "Organization not found")

    def test_skips_ambiguous_name_matches(self) -> None:
        membership_type = self._membership_type(code="bronze", category_id="sponsorship")
        for idx in range(2):
            Organization.objects.create(
                name="Twin Org",
                country_code="US",
                business_contact_name=f"Biz {idx}",
                business_contact_email=f"biz{idx}@example.com",
                pr_marketing_contact_name=f"PR {idx}",
                pr_marketing_contact_email=f"pr{idx}@example.com",
                technical_contact_name=f"Tech {idx}",
                technical_contact_email=f"tech{idx}@example.com",
                website=f"https://example{idx}.com",
                website_logo=f"https://example{idx}.com/logo.png",
            )

        dataset = Dataset(headers=["organization_name"])
        dataset.append(["twin org"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        resource.before_import(dataset)

        decision, reason, _organization, _row_note, _responses, _start_at, _end_at = resource._decision_for_row(dataset.dict[0])
        self.assertEqual(decision, "SKIP")
        self.assertIn("Ambiguous organization name", reason)

    def test_before_import_uses_shared_resolve_column_header(self) -> None:
        membership_type = self._membership_type(code="resolvehelper", category_id="sponsorship")
        dataset = Dataset(headers=["organization_id", "organization_name"])
        dataset.append(["1", "Known Org"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )

        with patch.object(
            organization_membership_csv_import,
            "resolve_column_header",
            wraps=resolve_column_header,
        ) as resolve_header:
            resource.before_import(dataset)

        self.assertGreaterEqual(resolve_header.call_count, 5)

    def test_before_import_resolves_question_headers_from_overrides(self) -> None:
        membership_type = self._membership_type(code="questionoverride", category_id="sponsorship")
        dataset = Dataset(headers=["organization_id", "Sponsor answer"])
        dataset.append(["1", "Yes"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
            question_column_overrides={"q_sponsorship_details_column": "Sponsor answer"},
        )

        resource.before_import(dataset)

        self.assertEqual(
            resource._question_header_by_name,
            {"Sponsorship details": "Sponsor answer"},
        )

    def test_before_import_auto_detects_question_headers_for_membership_type(self) -> None:
        membership_type = self._membership_type(code="questionautodetect", category_id="sponsorship")
        dataset = Dataset(headers=["organization_id", "Sponsorship details"])
        dataset.append(["1", "Answer"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )

        resource.before_import(dataset)

        self.assertEqual(
            resource._question_header_by_name,
            {"Sponsorship details": "Sponsorship details"},
        )

    def test_before_import_auto_detects_question_headers_from_question_title(self) -> None:
        membership_type = self._membership_type(code="questiontitleautodetect", category_id="sponsorship")
        dataset = Dataset(
            headers=[
                "organization_id",
                "Please describe your organization's sponsorship goals and planned community participation.",
            ]
        )
        dataset.append(["1", "We sponsor docs and events"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )

        resource.before_import(dataset)

        self.assertEqual(
            resource._question_header_by_name,
            {
                "Sponsorship details": "Please describe your organization's sponsorship goals and planned community participation.",
            },
        )

    def test_row_responses_uses_only_mapped_question_headers(self) -> None:
        membership_type = self._membership_type(code="questionresponses", category_id="sponsorship")
        dataset = Dataset(headers=["organization_id", "Sponsorship details", "Unmapped column"])
        dataset.append(["1", "Detailed sponsorship answer", "should-not-be-copied"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        resource.before_import(dataset)

        responses = resource._row_responses(dataset.dict[0])

        self.assertEqual(responses, [{"Sponsorship details": "Detailed sponsorship answer"}])

    def test_unmatched_export_sanitizes_formula_cells(self) -> None:
        membership_type = self._membership_type(code="sanitizeorg", category_id="sponsorship")
        dataset = Dataset(headers=["organization_name"])
        dataset.append(["=SUM(1,2)"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        result = resource.import_data(dataset, dry_run=True, raise_errors=True)

        csv_content = str(getattr(result, "unmatched_csv_content", ""))
        self.assertTrue(csv_content)

        rows = list(csv.DictReader(io.StringIO(csv_content)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["organization_name"], "'=SUM(1,2)")

    def test_confirm_replaces_within_category_and_default_expiry_uses_import_time_not_start_date(self) -> None:
        silver = self._membership_type(code="silver", category_id="sponsorship")
        gold = self._membership_type(code="gold", category_id="sponsorship")

        org = Organization.objects.create(
            name="Acme",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        Membership.objects.create(
            target_organization=org,
            membership_type=silver,
            expires_at=timezone.now().astimezone(datetime.UTC) - datetime.timedelta(days=1),
        )

        dataset = Dataset(headers=["organization_id", "membership_start_date"])
        dataset.append([str(org.pk), "2020-01-02"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=gold,
            actor_username="alex",
        )

        lower_approved_at = timezone.now().astimezone(datetime.UTC)
        with patch("core.models.Membership.replace_within_category", wraps=Membership.replace_within_category) as replace_within_category:
            resource.import_data(dataset, dry_run=False, raise_errors=True)
        upper_approved_at = timezone.now().astimezone(datetime.UTC)

        self.assertGreaterEqual(replace_within_category.call_count, 1)

        memberships = Membership.objects.filter(target_organization=org)
        self.assertEqual(memberships.count(), 1)
        imported = memberships.get()
        self.assertEqual(imported.membership_type, gold)
        self.assertEqual(imported.created_at, datetime.datetime(2020, 1, 2, 0, 0, 0, tzinfo=datetime.UTC))
        self.assertIsNotNone(imported.expires_at)
        imported_expires_at = imported.expires_at
        assert imported_expires_at is not None

        expected_lower = MembershipLog.expiry_for_approval_at(approved_at=lower_approved_at)
        expected_upper = MembershipLog.expiry_for_approval_at(approved_at=upper_approved_at)
        self.assertTrue(imported_expires_at >= expected_lower)
        self.assertTrue(imported_expires_at <= expected_upper)

        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.approved,
                membership_type=gold,
                target_organization=org,
                actor_username="alex",
            ).exists()
        )

    def test_confirm_creates_committee_note_on_created_membership_request_and_sends_no_email(self) -> None:
        membership_type = self._membership_type(code="platinum", category_id="sponsorship")

        org = Organization.objects.create(
            name="Org Notes",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        dataset = Dataset(headers=["organization_id", "committee_notes"])
        dataset.append([str(org.pk), "Imported by committee"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        resource.import_data(dataset, dry_run=False, raise_errors=True)

        membership_request = MembershipRequest.objects.filter(
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
            decided_by_username="alex",
        ).first()
        self.assertIsNotNone(membership_request)
        assert membership_request is not None

        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                username="alex",
                content="[Import] Imported by committee",
            ).exists()
        )
        self.assertEqual(Email.objects.count(), 0)

    def test_confirm_result_totals_reports_new_and_skipped_rows(self) -> None:
        membership_type = self._membership_type(code="diamond", category_id="sponsorship")
        org = Organization.objects.create(
            name="Totals Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        dataset = Dataset(headers=["organization_id", "organization_name"])
        dataset.append([str(org.pk), org.name])
        dataset.append(["999999", "Missing Org"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        result = resource.import_data(dataset, dry_run=False, raise_errors=True)

        self.assertEqual(result.totals["new"], 1)
        self.assertEqual(result.totals["skip"], 1)
        self.assertEqual(result.totals["update"], 0)
        self.assertEqual(result.totals["delete"], 0)
        self.assertEqual(result.totals["error"], 0)
        self.assertEqual(result.totals["invalid"], 0)

    def test_after_import_logs_summary(self) -> None:
        membership_type = self._membership_type(code="logsummary", category_id="sponsorship")
        org = Organization.objects.create(
            name="Summary Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        dataset = Dataset(headers=["organization_id", "organization_name"])
        dataset.append([str(org.pk), org.name])
        dataset.append(["999999", "Missing Org"])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )

        with patch("core.organization_membership_csv_import.logger.info") as logger_info:
            resource.import_data(dataset, dry_run=True, raise_errors=True)

        self.assertTrue(logger_info.called)

    def test_import_data_generates_per_run_batch_id_and_emits_ops07_batch_event(self) -> None:
        membership_type = self._membership_type(code="batchaudit", category_id="sponsorship")

        first_org = Organization.objects.create(
            name="Batch Org One",
            country_code="US",
            business_contact_name="Biz One",
            business_contact_email="biz1@example.com",
            pr_marketing_contact_name="PR One",
            pr_marketing_contact_email="pr1@example.com",
            technical_contact_name="Tech One",
            technical_contact_email="tech1@example.com",
            website="https://batch-one.example.com",
            website_logo="https://batch-one.example.com/logo.png",
        )
        second_org = Organization.objects.create(
            name="Batch Org Two",
            country_code="US",
            business_contact_name="Biz Two",
            business_contact_email="biz2@example.com",
            pr_marketing_contact_name="PR Two",
            pr_marketing_contact_email="pr2@example.com",
            technical_contact_name="Tech Two",
            technical_contact_email="tech2@example.com",
            website="https://batch-two.example.com",
            website_logo="https://batch-two.example.com/logo.png",
        )

        first_dataset = Dataset(headers=["organization_id", "organization_name"])
        first_dataset.append([str(first_org.pk), first_org.name])

        second_dataset = Dataset(headers=["organization_id", "organization_name"])
        second_dataset.append([str(second_org.pk), second_org.name])

        with patch("core.organization_membership_csv_import.logger.info") as logger_info:
            first_preview_resource = OrganizationMembershipCSVImportResource(
                membership_type=membership_type,
                actor_username="alex",
            )
            first_preview_result = first_preview_resource.import_data(first_dataset, dry_run=True, raise_errors=True)

            first_resource = OrganizationMembershipCSVImportResource(
                membership_type=membership_type,
                actor_username="alex",
            )
            first_result = first_resource.import_data(first_dataset, dry_run=False, raise_errors=True)

            second_preview_resource = OrganizationMembershipCSVImportResource(
                membership_type=membership_type,
                actor_username="alex",
            )
            second_preview_result = second_preview_resource.import_data(second_dataset, dry_run=True, raise_errors=True)

            second_resource = OrganizationMembershipCSVImportResource(
                membership_type=membership_type,
                actor_username="alex",
            )
            second_result = second_resource.import_data(second_dataset, dry_run=False, raise_errors=True)

        self.assertEqual(first_preview_result.totals["error"], 0)
        self.assertEqual(second_preview_result.totals["error"], 0)
        self.assertEqual(first_result.totals["error"], 0)
        self.assertEqual(second_result.totals["error"], 0)

        batch_calls = [
            call
            for call in logger_info.call_args_list
            if call.args and "event=astra.membership.csv_import.batch_applied" in str(call.args[0])
        ]

        self.assertEqual(len(batch_calls), 2)
        batch_messages = [str(call.args[0]) for call in batch_calls]
        first_batch = uuid.UUID(batch_messages[0].split("batch_id=", 1)[1].split(" ", 1)[0])
        second_batch = uuid.UUID(batch_messages[1].split("batch_id=", 1)[1].split(" ", 1)[0])
        self.assertNotEqual(first_batch, second_batch)

        for message in batch_messages:
            self.assertIn("component=membership", message)
            self.assertIn("outcome=applied", message)
            self.assertIn("correlation_id=", message)
            self.assertIn("rows_total=1", message)
            self.assertIn("rows_applied=1", message)
            self.assertIn("rows_failed=0", message)

        required_extra_keys = {
            "event",
            "component",
            "outcome",
            "batch_id",
            "rows_total",
            "rows_applied",
            "rows_failed",
            "correlation_id",
        }
        for batch_call in batch_calls:
            extra = batch_call.kwargs["extra"]
            self.assertEqual(set(extra.keys()), required_extra_keys)
            self.assertEqual(extra["event"], "astra.membership.csv_import.batch_applied")
            self.assertEqual(extra["component"], "membership")
            self.assertEqual(extra["outcome"], "applied")
            self.assertEqual(extra["rows_total"], 1)
            self.assertEqual(extra["rows_applied"], 1)
            self.assertEqual(extra["rows_failed"], 0)
            self.assertEqual(str(extra["batch_id"]), str(extra["correlation_id"]))

        first_logs = MembershipLog.objects.filter(target_organization=first_org)
        second_logs = MembershipLog.objects.filter(target_organization=second_org)
        self.assertTrue(first_logs.exists())
        self.assertTrue(second_logs.exists())
        self.assertEqual(set(first_logs.values_list("import_batch_id", flat=True)), {first_batch})
        self.assertEqual(set(second_logs.values_list("import_batch_id", flat=True)), {second_batch})

    def test_required_optional_columns_are_derived_from_column_specs(self) -> None:
        specs = (
            organization_membership_csv_import._ColumnSpec(
                field_name="required_column",
                logical_name="required_column",
                help_text="required help",
                required=True,
                display_name="required_display",
            ),
            organization_membership_csv_import._ColumnSpec(
                field_name="optional_column",
                logical_name="optional_column",
                help_text="optional help",
                required=False,
                display_name="optional_display",
            ),
        )

        with patch.object(organization_membership_csv_import, "_COLUMN_SPECS", specs):
            self.assertEqual(
                organization_membership_csv_import.required_organization_membership_csv_columns(),
                ("required_display",),
            )
            self.assertEqual(
                organization_membership_csv_import.optional_organization_membership_csv_columns(),
                ("optional_display",),
            )

    def test_confirm_does_not_create_placeholder_organizations(self) -> None:
        membership_type = self._membership_type(code="nodummyorg", category_id="sponsorship")
        org = Organization.objects.create(
            name="No Placeholder Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        before_count = Organization.objects.count()

        dataset = Dataset(headers=["organization_id"])
        dataset.append([str(org.pk)])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )
        result = resource.import_data(dataset, dry_run=False, raise_errors=True)

        self.assertEqual(result.totals["new"], 1)
        self.assertEqual(Organization.objects.count(), before_count)
        self.assertFalse(Organization.objects.filter(name="").exists())

    def test_confirm_save_instance_does_not_delegate_to_modelresource_super(self) -> None:
        membership_type = self._membership_type(code="superhook", category_id="sponsorship")
        org = Organization.objects.create(
            name="Super Call Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        dataset = Dataset(headers=["organization_id"])
        dataset.append([str(org.pk)])

        resource = OrganizationMembershipCSVImportResource(
            membership_type=membership_type,
            actor_username="alex",
        )

        with patch.object(
            resources.ModelResource,
            "save_instance",
            autospec=True,
            wraps=resources.ModelResource.save_instance,
        ) as super_save_instance:
            resource.import_data(dataset, dry_run=False, raise_errors=True)

        self.assertEqual(super_save_instance.call_count, 0)


class OrganizationMembershipCSVImportAdminFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()
        ensure_email_templates()

    def _membership_type(self, *, code: str = "gold") -> MembershipType:
        from core.models import MembershipTypeCategory

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True)
        membership_type, _ = MembershipType.objects.update_or_create(
            code=code,
            defaults={
                "name": code.title(),
                "group_cn": f"almalinux-{code}",
                "category_id": "sponsorship",
                "enabled": True,
                "sort_order": 0,
            },
        )
        return membership_type

    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_import_formats_csv_only(self) -> None:
        site = AdminSite()
        admin_instance = OrganizationMembershipCSVImportLinkAdmin(OrganizationMembershipCSVImportLink, site)

        self.assertEqual(admin_instance.get_import_formats(), [base_formats.CSV])

    def test_preview_confirm_and_unmatched_download(self) -> None:
        membership_type = self._membership_type(code="gold")
        org = Organization.objects.create(
            name="Known Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        csv_buffer = io.StringIO()
        csv_buffer.write("organization_id,organization_name,membership_start_date,committee_notes\n")
        csv_buffer.write(f"{org.pk},Known Org,2024-01-02,Known row\n")
        csv_buffer.write("999999,Missing Org,2024-01-02,Missing row\n")
        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            csv_buffer.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Organizations to Import")
        self.assertContains(preview, "Skipped")

        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None
        self.assertIn("unmatched_download_url", preview.context)
        self.assertIsNotNone(preview.context.get("matches_page_obj"))
        self.assertIsNotNone(preview.context.get("skipped_page_obj"))
        unmatched_download_url = preview.context.get("unmatched_download_url")
        self.assertTrue(unmatched_download_url)
        assert isinstance(unmatched_download_url, str)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            confirm = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_process_import"),
                data=dict(confirm_form.initial),
                follow=False,
            )
        self.assertEqual(confirm.status_code, 302)

        imported_membership = Membership.objects.get(target_organization=org)
        self.assertEqual(imported_membership.membership_type, membership_type)
        self.assertEqual(Email.objects.count(), 0)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            unmatched_download = self.client.get(unmatched_download_url)
        self.assertEqual(unmatched_download.status_code, 200)
        decoded = unmatched_download.content.decode("utf-8")
        self.assertIn("Missing Org", decoded)
        self.assertIn("Organization not found", decoded)

    def test_import_page_hides_column_selectors_until_file_selected(self) -> None:
        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            response = self.client.get(reverse("admin:core_organizationmembershipcsvimportlink_import"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js-column-selector-row")
        self.assertContains(response, 'style="display: none;"')
        self.assertContains(response, "window.csvImportHeaders || {}")

    def test_live_import_persists_membership_request_question_responses(self) -> None:
        membership_type = self._membership_type(code="questionresponses")
        org = Organization.objects.create(
            name="Question Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            (
                "organization_id,organization_name,Sponsorship answer\n"
                f"{org.pk},{org.name},We sponsor events and docs\n"
            ).encode(),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                    "q_sponsorship_details_column": "Sponsorship answer",
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            confirm = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_process_import"),
                data=dict(confirm_form.initial),
                follow=False,
            )

        self.assertEqual(confirm.status_code, 302)

        membership_request = MembershipRequest.objects.get(
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )
        self.assertEqual(
            membership_request.responses,
            [{"Sponsorship details": "We sponsor events and docs"}],
        )

    def test_live_import_silver_auto_detects_sponsorship_details_from_question_title_header(self) -> None:
        membership_type = self._membership_type(code="silver")
        org = Organization.objects.create(
            name="Silver Question Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            (
                "organization_id,organization_name,Please describe your organization's sponsorship goals and planned community participation.\n"
                f"{org.pk},{org.name},We fund infra and documentation work\n"
            ).encode(),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            confirm = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_process_import"),
                data=dict(confirm_form.initial),
                follow=False,
            )

        self.assertEqual(confirm.status_code, 302)

        membership_request = MembershipRequest.objects.get(
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )
        self.assertEqual(
            membership_request.responses,
            [{"Sponsorship details": "We fund infra and documentation work"}],
        )

    def test_preview_initializes_selected_row_numbers_for_matches(self) -> None:
        membership_type = self._membership_type(code="selectable")
        org = Organization.objects.create(
            name="Selectable Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            (
                "organization_id,organization_name\n"
                f"{org.pk},{org.name}\n"
                "999999,Missing Org\n"
            ).encode(),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None
        selected_row_numbers = str(confirm_form.initial.get("selected_row_numbers") or "").strip()
        self.assertTrue(
            selected_row_numbers,
            msg=(
                f"selected_row_numbers empty; all_match_row_numbers_csv={preview.context.get('all_match_row_numbers_csv')!r}; "
                f"preview_summary={preview.context.get('preview_summary')!r}"
            ),
        )
        self.assertIn("1", selected_row_numbers)
        self.assertContains(preview, 'id="id_select_all_matches"')

    def test_confirm_success_message_reports_non_zero_import_totals(self) -> None:
        membership_type = self._membership_type(code="platinum")
        org = Organization.objects.create(
            name="Message Totals Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        csv_buffer = io.StringIO()
        csv_buffer.write("organization_id,organization_name\n")
        csv_buffer.write(f"{org.pk},{org.name}\n")
        csv_buffer.write("999999,Missing Org\n")
        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            csv_buffer.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            confirm = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_process_import"),
                data=dict(confirm_form.initial),
                follow=True,
            )

        self.assertEqual(confirm.status_code, 200)
        self.assertContains(confirm, "1 new, 0 updated, 0 deleted and 0 skipped")

    def test_confirm_can_select_subset_of_matches(self) -> None:
        membership_type = self._membership_type(code="subset")
        org_one = Organization.objects.create(
            name="Subset One",
            country_code="US",
            business_contact_name="Biz One",
            business_contact_email="biz1@example.com",
            pr_marketing_contact_name="PR One",
            pr_marketing_contact_email="pr1@example.com",
            technical_contact_name="Tech One",
            technical_contact_email="tech1@example.com",
            website="https://subset-one.example.com",
            website_logo="https://subset-one.example.com/logo.png",
        )
        org_two = Organization.objects.create(
            name="Subset Two",
            country_code="US",
            business_contact_name="Biz Two",
            business_contact_email="biz2@example.com",
            pr_marketing_contact_name="PR Two",
            pr_marketing_contact_email="pr2@example.com",
            technical_contact_name="Tech Two",
            technical_contact_email="tech2@example.com",
            website="https://subset-two.example.com",
            website_logo="https://subset-two.example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            (
                "organization_id,organization_name\n"
                f"{org_one.pk},{org_one.name}\n"
                f"{org_two.pk},{org_two.name}\n"
            ).encode(),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None

        selected_all = str(confirm_form.initial.get("selected_row_numbers") or "").strip()
        self.assertTrue(selected_all)
        selected_parts = [item.strip() for item in selected_all.split(",") if item.strip()]
        self.assertGreaterEqual(len(selected_parts), 2)

        confirm_data = dict(confirm_form.initial)
        confirm_data["selected_row_numbers"] = selected_parts[0]

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            confirm = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_process_import"),
                data=confirm_data,
                follow=False,
            )

        self.assertEqual(confirm.status_code, 302)
        self.assertTrue(Membership.objects.filter(target_organization=org_one, membership_type=membership_type).exists())
        self.assertFalse(Membership.objects.filter(target_organization=org_two, membership_type=membership_type).exists())

    def test_preview_confirm_form_initial_copies_dynamic_question_column_values(self) -> None:
        membership_type = self._membership_type(code="initialmapping")
        org = Organization.objects.create(
            name="Initial Mapping Org",
            country_code="US",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website="https://example.com",
            website_logo="https://example.com/logo.png",
        )

        self._login_as_freeipa_admin("alex")
        admin_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        uploaded = SimpleUploadedFile(
            "organization-memberships.csv",
            (
                "organization_id,organization_name,Sponsorship answer\n"
                f"{org.pk},{org.name},Mapped\n"
            ).encode(),
            content_type="text/csv",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            preview = self.client.post(
                reverse("admin:core_organizationmembershipcsvimportlink_import"),
                data={
                    "resource": "0",
                    "format": "0",
                    "membership_type": membership_type.code,
                    "import_file": uploaded,
                    "q_sponsorship_details_column": "Sponsorship answer",
                },
                follow=False,
            )

        self.assertEqual(preview.status_code, 200)
        confirm_form = preview.context.get("confirm_form")
        self.assertIsNotNone(confirm_form)
        assert confirm_form is not None
        self.assertEqual(confirm_form.initial.get("q_sponsorship_details_column"), "Sponsorship answer")

    def test_get_import_resource_kwargs_passes_question_column_overrides(self) -> None:
        membership_type = self._membership_type(code="kwargmapping")
        admin_instance = OrganizationMembershipCSVImportLinkAdmin(OrganizationMembershipCSVImportLink, AdminSite())

        request: Any = RequestFactory().post("/admin/core/organizationmembershipcsvimportlink/process_import/")
        request.user = SimpleNamespace(get_username=lambda: "alex")

        import_form = SimpleNamespace(
            cleaned_data={
                "membership_type": membership_type,
                "organization_id_column": "organization_id",
                "q_sponsorship_details_column": "Sponsorship answer",
                "q_domain_column": "Mirror domain",
            }
        )

        kwargs = admin_instance.get_import_resource_kwargs(request, form=import_form)

        self.assertEqual(kwargs["membership_type"], membership_type)
        self.assertEqual(
            kwargs["question_column_overrides"],
            {
                "q_sponsorship_details_column": "Sponsorship answer",
                "q_domain_column": "Mirror domain",
            },
        )
