
from django.test import TestCase

from core.templated_email import render_templated_email_preview


class TemplatedEmailPreviewPlaceholderTests(TestCase):
    def test_preview_uses_placeholder_for_empty_context_values(self) -> None:
        rendered = render_templated_email_preview(
            subject="Hi {{ election_name }}",
            html_content="",
            text_content="",
            context={"election_name": ""},
        )
        self.assertEqual(rendered["subject"], "Hi -election_name-")

    def test_preview_uses_placeholder_for_missing_keys_even_when_context_provided(self) -> None:
        rendered = render_templated_email_preview(
            subject="Hi {{ election_name }} {{ election_description }}",
            html_content="",
            text_content="",
            context={"election_name": "Example"},
        )
        self.assertEqual(rendered["subject"], "Hi Example -election_description-")
