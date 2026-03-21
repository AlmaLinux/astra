import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from post_office.models import Email, EmailTemplate

from core.membership_notifications import (
    already_sent_today,
    membership_requests_url,
    oldest_pending_membership_request_wait_time,
    organization_sponsor_notification_recipient_email,
    would_queue_membership_pending_requests_notification,
)
from core.models import MembershipRequest, MembershipType, Organization
from core.public_urls import normalize_public_base_url


class AlreadySentTodayTests(TestCase):
    def _create_membership_type(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

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
        base = normalize_public_base_url(settings.PUBLIC_BASE_URL)
        if not base:
            self.skipTest("PUBLIC_BASE_URL is empty in this environment")

        self.assertEqual(
            membership_requests_url(base_url=settings.PUBLIC_BASE_URL),
            f"{base}/membership/requests/",
        )

    def test_pending_requests_dedupe_policy_uses_thursday_anchor(self) -> None:
        template = EmailTemplate.objects.create(
            name="membership-committee-pending-requests-dedupe",
            subject="Pending requests",
            content="Pending requests",
        )

        thursday = datetime.date(2026, 1, 8)
        next_monday = datetime.date(2026, 1, 12)

        self.assertTrue(
            would_queue_membership_pending_requests_notification(
                force=False,
                template_name=template.name,
                today=thursday,
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

        self.assertTrue(
            would_queue_membership_pending_requests_notification(
                force=False,
                template_name=template.name,
                today=thursday,
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
            created=timezone.make_aware(datetime.datetime(2026, 1, 8, 10, 0, 0)),
        )

        self.assertFalse(
            would_queue_membership_pending_requests_notification(
                force=False,
                template_name=template.name,
                today=next_monday,
            )
        )

    def test_oldest_pending_membership_request_wait_time_uses_pending_requests_only(self) -> None:
        frozen_now = timezone.make_aware(datetime.datetime(2026, 1, 21, 12, 0, 0))
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()

            pending = MembershipRequest.objects.create(
                requested_username="pending-user",
                membership_type_id="individual",
            )
            MembershipRequest.objects.filter(pk=pending.pk).update(
                requested_at=timezone.make_aware(datetime.datetime(2026, 1, 9, 12, 0, 0)),
            )

            approved = MembershipRequest.objects.create(
                requested_username="approved-user",
                membership_type_id="individual",
                status=MembershipRequest.Status.approved,
            )
            MembershipRequest.objects.filter(pk=approved.pk).update(
                requested_at=timezone.make_aware(datetime.datetime(2025, 12, 22, 12, 0, 0)),
            )

            self.assertEqual(oldest_pending_membership_request_wait_time(), 12)

    def test_oldest_pending_membership_request_wait_time_skips_recent_requests(self) -> None:
        frozen_now = timezone.make_aware(datetime.datetime(2026, 1, 21, 12, 0, 0))
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()

            pending = MembershipRequest.objects.create(
                requested_username="pending-user",
                membership_type_id="individual",
            )
            MembershipRequest.objects.filter(pk=pending.pk).update(
                requested_at=timezone.make_aware(datetime.datetime(2026, 1, 9, 13, 0, 0)),
            )

            approved = MembershipRequest.objects.create(
                requested_username="approved-user",
                membership_type_id="individual",
                status=MembershipRequest.Status.approved,
            )
            MembershipRequest.objects.filter(pk=approved.pk).update(
                requested_at=timezone.make_aware(datetime.datetime(2025, 12, 22, 12, 0, 0)),
            )

            self.assertIsNone(oldest_pending_membership_request_wait_time())


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
