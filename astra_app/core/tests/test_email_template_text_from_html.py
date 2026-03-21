
from django.test import SimpleTestCase


class EmailTemplateTextFromHTMLTests(SimpleTestCase):
    def test_preserves_django_template_tags_and_drops_images(self) -> None:
        from core.migration_helpers.email_template_text import text_from_html

        html = (
            "<p>{% if oldest_wait_time %} The oldest request has been waiting for "
            "<strong>{{ oldest_wait_time }} days</strong>.{% endif %}</p>"
            "<p><img src='https://example.com/x.png' alt='X'>Should keep this.</p>"
        )

        text = text_from_html(html)

        self.assertIn("{% if oldest_wait_time %}", text)
        self.assertIn("{% endif %}", text)
        self.assertIn("The oldest request has been waiting for", text)
        self.assertIn("{{ oldest_wait_time }}", text)
        self.assertIn("**{{ oldest_wait_time }} days**", text)
        self.assertIn("Should keep this.", text)
        self.assertNotIn("example.com/x.png", text)
        self.assertNotIn("alt=", text)

    def test_signature_normalization_matches_js(self) -> None:
        from core.migration_helpers.email_template_text import text_from_html

        html = "<p><em>The AlmaLinux Team</em></p>"
        self.assertEqual(text_from_html(html), "-- The AlmaLinux Team")

    def test_inline_formatting_and_whitespace(self) -> None:
        from core.migration_helpers.email_template_text import text_from_html

        html = "<p>election: <strong>X</strong> <em>Y</em> <u>Z</u></p>"
        text = text_from_html(html)
        self.assertIn("election:", text)
        self.assertIn("**X**", text)
        self.assertIn("*Y*", text)
        self.assertIn("_Z_", text)
        # Critical: whitespace-only text nodes between inline tags must not be dropped.
        self.assertIn("**X** *Y*", text)

    def test_links_render_markdown_style(self) -> None:
        from core.migration_helpers.email_template_text import text_from_html

        html = "<p>See <a href='https://example.com'>Example</a>.</p>"
        self.assertIn("[Example](https://example.com)", text_from_html(html))

    def test_headings_hr_blockquote_and_lists(self) -> None:
        from core.migration_helpers.email_template_text import text_from_html

        html = (
            "<h3>Title</h3>"
            "<p>Para</p>"
            "<hr>"
            "<blockquote><p>Quoted</p></blockquote>"
            "<ul><li>One</li><li>Two</li></ul>"
        )
        text = text_from_html(html)
        self.assertIn("### Title", text)
        self.assertIn("Para", text)
        self.assertIn("---", text)
        self.assertIn("> Quoted", text)
        self.assertIn("- One", text)
        self.assertIn("- Two", text)

    def test_preformatted_text_is_preserved(self) -> None:
        from core.migration_helpers.email_template_text import text_from_html

        html = "<pre>line1\n  line2\n</pre>"
        text = text_from_html(html)
        self.assertIn("line1", text)
        self.assertIn("  line2", text)
