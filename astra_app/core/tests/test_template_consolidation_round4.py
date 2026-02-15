from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class Round4TemplateConsolidationTests(SimpleTestCase):
    def _template_source(self, template_name: str) -> str:
        template_path = Path(settings.BASE_DIR) / "core" / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_expiry_and_termination_render_template_uses_single_canonical_block(self) -> None:
        source = self._template_source("_expiry_and_termination_actions_render.html")

        self.assertNotIn("{% elif organization %}", source)

    def test_membership_request_actions_invokes_inner_template_once(self) -> None:
        source = self._template_source("_membership_request_actions.html")

        include_stmt = "{% include 'core/_membership_request_actions_inner.html'"
        self.assertEqual(source.count(include_stmt), 1)

    def test_membership_request_target_display_is_shared_include(self) -> None:
        requester_cell_source = self._template_source("_membership_request_requester_cell.html")
        detail_source = self._template_source("membership_request_detail.html")

        include_stmt = "{% include 'core/_membership_request_target_display.html'"
        self.assertIn(include_stmt, requester_cell_source)
        self.assertIn(include_stmt, detail_source)

    def test_membership_request_actions_avoids_target_sentinel_fallback(self) -> None:
        source = self._template_source("_membership_request_actions.html")

        self.assertNotIn("organization_display_name|default:membership_request.requested_username", source)

    def test_phase9_bulk_pages_use_shared_bulk_table_actions_module(self) -> None:
        invitations = self._template_source("account_invitations.html")
        requests = self._template_source("membership_requests.html")

        self.assertIn("core/js/bulk_table_actions.js", invitations)
        self.assertIn("core/js/bulk_table_actions.js", requests)
        self.assertNotIn("function setupBulk", invitations)
        self.assertNotIn("function setupBulk", requests)

    def test_phase9_membership_shared_modals_compose_canonical_modal_includes(self) -> None:
        source = self._template_source("_membership_request_shared_modals.html")

        self.assertIn("_modal_confirm.html", source)
        self.assertIn("_modal_preset_textarea.html", source)
        self.assertNotIn("<div class=\"modal fade\"", source)

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
