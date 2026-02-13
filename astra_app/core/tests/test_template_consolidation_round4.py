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
