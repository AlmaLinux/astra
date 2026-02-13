import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from post_office.models import Email, EmailTemplate

from core.membership_notifications import (
    already_sent_today,
    membership_requests_url,
    organization_sponsor_notification_recipient_email,
    would_queue_membership_pending_requests_notification,
)
from core.models import Organization


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

    def test_membership_requests_url_returns_relative_path_when_public_base_missing(self) -> None:
        self.assertEqual(membership_requests_url(base_url=""), "/membership/requests/")

    def test_membership_requests_url_uses_public_base_when_present(self) -> None:
        base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
        if not base:
            self.skipTest("PUBLIC_BASE_URL is empty in this environment")

        self.assertEqual(
            membership_requests_url(base_url=settings.PUBLIC_BASE_URL),
            f"{base}/membership/requests/",
        )

    def test_pending_requests_dedupe_policy_is_monday_daily_otherwise_weekly(self) -> None:
        template = EmailTemplate.objects.create(
            name="membership-committee-pending-requests-dedupe",
            subject="Pending requests",
            content="Pending requests",
        )

        monday = datetime.date(2026, 1, 5)
        tuesday = datetime.date(2026, 1, 6)

        self.assertTrue(
            would_queue_membership_pending_requests_notification(
                force=False,
                template_name=template.name,
                today=monday,
            )
        )

        email = Email.objects.create(
            from_email="noreply@example.com",
            to="committee@example.com",
            subject="Pending requests",
            message="Queued",
            template=template,
        )
        Email.objects.filter(pk=email.pk).update(
            created=timezone.make_aware(datetime.datetime(2026, 1, 5, 10, 0, 0)),
        )

        self.assertFalse(
            would_queue_membership_pending_requests_notification(
                force=False,
                template_name=template.name,
                today=monday,
            )
        )
        self.assertFalse(
            would_queue_membership_pending_requests_notification(
                force=False,
                template_name=template.name,
                today=tuesday,
            )
        )


class OrganizationSponsorRecipientTests(TestCase):
    def test_returns_representative_email_when_available(self) -> None:
        organization = Organization.objects.create(
            name="Example Org",
            representative="org-rep",
            business_contact_email="fallback@example.com",
        )

        rep = type("_Rep", (), {"email": "rep@example.com"})()
        with patch("core.membership_notifications.FreeIPAUser.get", return_value=rep):
            recipient, warning = organization_sponsor_notification_recipient_email(
                organization=organization,
                notification_kind="org submitted",
            )

        self.assertEqual("rep@example.com", recipient)
        self.assertIsNone(warning)

    def test_falls_back_to_primary_contact_when_representative_missing_email(self) -> None:
        organization = Organization.objects.create(
            name="Example Org",
            representative="org-rep",
            business_contact_email="fallback@example.com",
        )

        rep = type("_Rep", (), {"email": ""})()
        with patch("core.membership_notifications.FreeIPAUser.get", return_value=rep):
            recipient, warning = organization_sponsor_notification_recipient_email(
                organization=organization,
                notification_kind="org expiring-soon",
            )

        self.assertEqual("fallback@example.com", recipient)
        self.assertIsNone(warning)

    def test_emits_warning_only_when_no_recipient_after_fallback(self) -> None:
        organization = Organization.objects.create(
            name="No Contact Org",
            representative="org-rep",
            business_contact_email="",
            pr_marketing_contact_email="",
            technical_contact_email="",
        )

        rep = type("_Rep", (), {"email": ""})()
        with patch("core.membership_notifications.FreeIPAUser.get", return_value=rep):
            recipient, warning = organization_sponsor_notification_recipient_email(
                organization=organization,
                notification_kind="org expired-cleanup",
            )

        self.assertEqual("", recipient)
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("organization id", warning)
        self.assertIn("org expired-cleanup", warning)
