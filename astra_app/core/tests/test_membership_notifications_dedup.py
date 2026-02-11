from django.test import TestCase
from post_office.models import Email, EmailTemplate

from core.membership_notifications import already_sent_today


class AlreadySentTodayTests(TestCase):
    def test_returns_true_for_matching_template_recipient_context(self) -> None:
        template = EmailTemplate.objects.create(
            name="dedup-template-true",
            subject="Subject",
            content="Body",
        )
        Email.objects.create(
            from_email="noreply@example.com",
            to="user@example.com",
            subject="Subject",
            message="Body",
            template=template,
            context={"membership_type_code": "gold"},
        )

        self.assertTrue(
            already_sent_today(
                template_name="dedup-template-true",
                recipient_email="user@example.com",
                extra_filters={"context__membership_type_code": "gold"},
            )
        )

    def test_returns_false_for_mismatched_context(self) -> None:
        template = EmailTemplate.objects.create(
            name="dedup-template-false",
            subject="Subject",
            content="Body",
        )
        Email.objects.create(
            from_email="noreply@example.com",
            to="user@example.com",
            subject="Subject",
            message="Body",
            template=template,
            context={"membership_type_code": "gold"},
        )

        self.assertFalse(
            already_sent_today(
                template_name="dedup-template-false",
                recipient_email="user@example.com",
                extra_filters={"context__membership_type_code": "silver"},
            )
        )
