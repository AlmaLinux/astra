from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class Round4TemplateConsolidationTests(SimpleTestCase):
    def _template_source(self, template_name: str) -> str:
        template_path = Path(settings.BASE_DIR) / "core" / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def _python_source(self, relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_expiry_and_termination_render_template_uses_single_canonical_block(self) -> None:
        source = self._template_source("_expiry_and_termination_actions_render.html")

        self.assertNotIn("{% elif organization %}", source)

    def test_membership_request_detail_uses_vue_actions_root_contract(self) -> None:
        source = self._template_source("membership_request_detail.html")

        self.assertIn("data-membership-request-actions-root", source)
        self.assertNotIn("_membership_request_actions.html", source)

    def test_membership_request_target_display_is_shared_include(self) -> None:
        requester_cell_source = self._template_source("_membership_request_requester_cell.html")
        detail_source = self._template_source("membership_request_detail.html")

        include_stmt = "{% include 'core/_membership_request_target_display.html'"
        self.assertIn(include_stmt, requester_cell_source)
        self.assertIn(include_stmt, detail_source)

    def test_membership_request_detail_does_not_include_shared_modals_partial(self) -> None:
        source = self._template_source("membership_request_detail.html")

        self.assertNotIn("_membership_request_shared_modals.html", source)

    def test_phase9_bulk_pages_use_expected_action_modules(self) -> None:
        invitations = self._template_source("account_invitations_vue.html")
        requests = self._template_source("membership_requests.html")

        self.assertIn("src/entrypoints/accountInvitations.ts", invitations)
        self.assertIn("src/entrypoints/membershipRequests.ts", requests)
        self.assertNotIn("core/js/bulk_table_actions.js", requests)
        self.assertNotIn("function setupBulk", invitations)
        self.assertNotIn("function setupBulk", requests)

    def test_membership_note_templates_use_default_api_backed_contract(self) -> None:
        detail_source = self._template_source("membership_request_detail.html")
        profile_source = self._template_source("_membership_profile_section.html")
        requester_cell_source = self._template_source("_membership_request_requester_cell.html")

        self.assertNotIn("api_backed_read=", detail_source)
        self.assertNotIn("api_backed_read=", profile_source)
        self.assertNotIn("preloaded_notes=", requester_cell_source)
        self.assertNotIn("fail_on_query_fallback=", requester_cell_source)

    def test_committee_note_reads_use_shared_context_helper(self) -> None:
        source = self._python_source("core/views_membership/committee.py")

        self.assertIn("def _membership_notes_read_context(", source)
        self.assertNotIn("def _membership_request_note_read_context(", source)
        self.assertNotIn("def _membership_notes_aggregate_read_context(", source)

    def test_expiry_modal_uses_utc_today_min_date_contract(self) -> None:
        shared_source = self._template_source("_expiry_and_termination_actions_shared.html")
        modal_source = self._template_source("_modal_expiry_and_terminate.html")

        self.assertIn("{% timezone 'UTC' %}", shared_source)
        self.assertIn('{% now "Y-m-d" as min_expiration_on_utc %}', shared_source)
        self.assertIn("min_value=min_expiration_on_utc", shared_source)
        self.assertIn('min="{{ min_value|default:\'\' }}"', modal_source)

    def test_phase9_compose_template_is_markup_only_and_uses_include_contract(self) -> None:
        compose = self._template_source("_templated_email_compose.html")
        send_mail = self._template_source("send_mail.html")
        election_edit = self._template_source("election_edit.html")
        email_template_edit = self._template_source("email_template_edit.html")

        self.assertNotIn("core/vendor/codemirror", compose)
        self.assertNotIn("<script src=", compose)
        self.assertNotIn("<style>", compose)

        self.assertIn("_templated_email_compose_assets_head.html", send_mail)
        self.assertIn("_templated_email_compose_assets_head.html", election_edit)
        self.assertIn("_templated_email_compose_assets_head.html", email_template_edit)

        self.assertIn("_templated_email_compose_assets_scripts.html", send_mail)
        self.assertIn("_templated_email_compose_assets_scripts.html", election_edit)
        self.assertIn("_templated_email_compose_assets_scripts.html", email_template_edit)
