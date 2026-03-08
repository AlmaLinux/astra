from types import SimpleNamespace
from unittest.mock import patch

from django import forms
from django.contrib.admin.sites import AdminSite
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.admin_import_preview_utils import (
    apply_selected_row_numbers_initial,
    build_import_preview_context,
    build_result_preview_transport,
)
from core.csv_import_utils import build_csv_header_choices, set_form_column_field_choices


class CsvHeaderChoiceHelpersTests(TestCase):
    class _Form(forms.Form):
        email_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
        name_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])

    def test_build_csv_header_choices_includes_auto_detect_first(self) -> None:
        choices = build_csv_header_choices(["Email", "Name"])

        self.assertEqual(
            choices,
            [
                ("", "Auto-detect"),
                ("Email", "Email"),
                ("Name", "Name"),
            ],
        )

    def test_set_form_column_field_choices_updates_requested_fields(self) -> None:
        form = self._Form()

        set_form_column_field_choices(
            form=form,
            field_names=("email_column", "name_column", "missing_column"),
            headers=["Email", "Name"],
        )

        expected = [
            ("", "Auto-detect"),
            ("Email", "Email"),
            ("Name", "Name"),
        ]
        self.assertEqual(form.fields["email_column"].choices, expected)
        self.assertEqual(form.fields["name_column"].choices, expected)


class ImportPreviewContextHelpersTests(TestCase):
    def test_build_import_preview_context_groups_and_paginates_rows(self) -> None:
        rows = [
            SimpleNamespace(import_type="new", instance=SimpleNamespace()),
            SimpleNamespace(import_type="", instance=SimpleNamespace(decision="IMPORT"), number="7"),
            SimpleNamespace(import_type="skip", instance=SimpleNamespace(decision="SKIP")),
        ]

        context = build_import_preview_context(
            valid_rows=rows,
            request_get={"matches_page": "1", "skipped_page": "1", "per_page": "100"},
            instance_decision_attr="decision",
        )

        self.assertEqual(context["preview_summary"], {"total": 3, "to_import": 2, "skipped": 1})
        self.assertEqual(context["match_row_numbers"], [1, 7])
        self.assertEqual(context["matches_page_obj"].paginator.count, 2)
        self.assertEqual(context["skipped_page_obj"].paginator.count, 1)

    def test_build_result_preview_transport_uses_result_rows_fallback_and_query_selection(self) -> None:
        class DummyRowResult:
            def __init__(self, import_type: str, number: int) -> None:
                self.import_type = import_type
                self.number = number
                self.instance = SimpleNamespace()

        transport = build_result_preview_transport(
            result=SimpleNamespace(rows=[DummyRowResult("new", 2), DummyRowResult("skip", 5)]),
            request_get={"selected_row_numbers": "5", "per_page": "100"},
            instance_decision_attr="decision",
            fallback_attr_name="rows",
        )

        self.assertEqual(transport["preview_summary"], {"total": 2, "to_import": 1, "skipped": 1})
        self.assertEqual(transport["all_match_row_numbers_csv"], "2")
        self.assertEqual(transport["selected_row_numbers"], "5")

    def test_build_result_preview_transport_accepts_callable_valid_rows(self) -> None:
        row = SimpleNamespace(import_type="new", number=8, instance=SimpleNamespace())

        transport = build_result_preview_transport(
            result=SimpleNamespace(valid_rows=lambda: [row]),
            request_get={},
            instance_decision_attr="decision",
        )

        self.assertEqual(transport["preview_summary"], {"total": 1, "to_import": 1, "skipped": 0})
        self.assertEqual(transport["match_row_numbers"], [8])

    def test_apply_selected_row_numbers_initial_updates_form_initial_and_field(self) -> None:
        class _ConfirmForm(forms.Form):
            selected_row_numbers = forms.CharField(required=False)

        form = _ConfirmForm(initial={"selected_row_numbers": "1"})

        apply_selected_row_numbers_initial(form, "3,4")

        self.assertEqual(form.initial["selected_row_numbers"], "3,4")
        self.assertEqual(form.fields["selected_row_numbers"].initial, "3,4")


class CsvImportAdminArchitectureTests(TestCase):
    def test_membership_org_membership_and_org_admins_inherit_base_csv_import_admin(self) -> None:
        from core.admin import (
            BaseCsvImportAdmin,
            MembershipCSVImportLinkAdmin,
            OrganizationCSVImportLinkAdmin,
            OrganizationMembershipCSVImportLinkAdmin,
        )

        self.assertTrue(issubclass(MembershipCSVImportLinkAdmin, BaseCsvImportAdmin))
        self.assertTrue(issubclass(OrganizationMembershipCSVImportLinkAdmin, BaseCsvImportAdmin))
        self.assertTrue(issubclass(OrganizationCSVImportLinkAdmin, BaseCsvImportAdmin))

    def test_has_import_permission_requires_active_staff_user(self) -> None:
        from core.admin import MembershipCSVImportLinkAdmin
        from core.models import MembershipCSVImportLink

        site = AdminSite()
        admin_instance = MembershipCSVImportLinkAdmin(MembershipCSVImportLink, site)
        request_factory = RequestFactory()

        staff_request = request_factory.get("/admin/core/membershipcsvimportlink/import/")
        staff_request.user = SimpleNamespace(is_active=True, is_staff=True)
        self.assertTrue(admin_instance.has_import_permission(staff_request))

        non_staff_request = request_factory.get("/admin/core/membershipcsvimportlink/import/")
        non_staff_request.user = SimpleNamespace(is_active=True, is_staff=False)
        self.assertFalse(admin_instance.has_import_permission(non_staff_request))

    def test_process_result_escapes_unmatched_message_label_html(self) -> None:
        from core.admin import MembershipCSVImportLinkAdmin
        from core.models import MembershipCSVImportLink

        site = AdminSite()
        admin_instance = MembershipCSVImportLinkAdmin(MembershipCSVImportLink, site)
        admin_instance.unmatched_message_label = "<img src=x onerror=alert(1)>"
        request = RequestFactory().get("/admin/core/membershipcsvimportlink/process_import/")
        request.user = SimpleNamespace(is_active=True, is_staff=True)
        result = SimpleNamespace(unmatched_download_url="/admin/core/download-unmatched/token/")

        with (
            patch("core.admin.messages.warning") as warning,
            patch("import_export.admin.ImportMixin.process_result", return_value=HttpResponse("ok")),
        ):
            response = admin_instance.process_result(result, request)

        self.assertEqual(response.status_code, 200)
        warning.assert_called_once()
        html_message = str(warning.call_args.args[1])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_message)
        self.assertNotIn("<img src=x onerror=alert(1)>", html_message)
