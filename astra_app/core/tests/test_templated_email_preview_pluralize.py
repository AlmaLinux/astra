
from django.test import SimpleTestCase

from core.templated_email import render_templated_email_preview


class TemplatedEmailPreviewPluralizeTests(SimpleTestCase):
    def test_pluralize_it_them_uses_placeholder_when_count_unknown(self) -> None:
        preview = render_templated_email_preview(
            subject="Do {{ count|pluralize:'it,them' }}",
            html_content="<p>{{ count|pluralize:'it,them' }}</p>",
            text_content="{{ count|pluralize:'it,them' }}",
            context={},
        )

        self.assertEqual(preview["subject"], "Do -it/them-")
        self.assertEqual(preview["html"], "<p>-it/them-</p>")
        self.assertEqual(preview["text"], "-it/them-")

    def test_pluralize_it_them_uses_it_when_count_is_one(self) -> None:
        preview = render_templated_email_preview(
            subject="Do {{ count|pluralize:'it,them' }}",
            html_content="<p>{{ count|pluralize:'it,them' }}</p>",
            text_content="{{ count|pluralize:'it,them' }}",
            context={"count": 1},
        )

        self.assertEqual(preview["subject"], "Do it")
        self.assertEqual(preview["html"], "<p>it</p>")
        self.assertEqual(preview["text"], "it")

    def test_pluralize_it_them_coerces_numeric_strings(self) -> None:
        preview_one = render_templated_email_preview(
            subject="{{ count|pluralize:'it,them' }}",
            html_content="",
            text_content="",
            context={"count": "1"},
        )
        self.assertEqual(preview_one["subject"], "it")

        preview_two = render_templated_email_preview(
            subject="{{ count|pluralize:'it,them' }}",
            html_content="",
            text_content="",
            context={"count": "2"},
        )
        self.assertEqual(preview_two["subject"], "them")

    def test_pluralize_it_them_uses_placeholder_when_context_missing_count(self) -> None:
        preview = render_templated_email_preview(
            subject="{{ count|pluralize:'it,them' }}",
            html_content="",
            text_content="",
            context={"other": "x"},
        )

        self.assertEqual(preview["subject"], "-it/them-")

    def test_pluralize_generic_pair(self) -> None:
        preview_unknown = render_templated_email_preview(
            subject="Do {{ count|pluralize:'thing,things' }}",
            html_content="",
            text_content="",
            context={},
        )
        self.assertEqual(preview_unknown["subject"], "Do -thing/things-")

        preview_one = render_templated_email_preview(
            subject="{{ count|pluralize:'thing,things' }}",
            html_content="",
            text_content="",
            context={"count": 1},
        )
        self.assertEqual(preview_one["subject"], "thing")

        preview_many = render_templated_email_preview(
            subject="{{ count|pluralize:'thing,things' }}",
            html_content="",
            text_content="",
            context={"count": 2},
        )
        self.assertEqual(preview_many["subject"], "things")

    def test_pluralize_default(self) -> None:
        preview_unknown = render_templated_email_preview(
            subject="{{ count|pluralize }}",
            html_content="",
            text_content="",
            context={},
        )
        self.assertEqual(preview_unknown["subject"], "-/s-")

        preview_one = render_templated_email_preview(
            subject="{{ count|pluralize }}",
            html_content="",
            text_content="",
            context={"count": 1},
        )
        self.assertEqual(preview_one["subject"], "")

        preview_many = render_templated_email_preview(
            subject="{{ count|pluralize }}",
            html_content="",
            text_content="",
            context={"count": 2},
        )
        self.assertEqual(preview_many["subject"], "s")

    def test_pluralize_one_arg(self) -> None:
        preview_unknown = render_templated_email_preview(
            subject="{{ count|pluralize:'es' }}",
            html_content="",
            text_content="",
            context={},
        )
        self.assertEqual(preview_unknown["subject"], "-/es-")

        preview_one = render_templated_email_preview(
            subject="{{ count|pluralize:'es' }}",
            html_content="",
            text_content="",
            context={"count": 1},
        )
        self.assertEqual(preview_one["subject"], "")

        preview_many = render_templated_email_preview(
            subject="{{ count|pluralize:'es' }}",
            html_content="",
            text_content="",
            context={"count": 2},
        )
        self.assertEqual(preview_many["subject"], "es")
