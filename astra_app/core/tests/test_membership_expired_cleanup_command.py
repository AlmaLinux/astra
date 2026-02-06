from __future__ import annotations

import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from post_office.models import EmailTemplate

from core.backends import FreeIPAUser
from core.models import Membership, MembershipLog, MembershipType, Organization, OrganizationSponsorship


class MembershipExpiredCleanupCommandTests(TestCase):
    def test_command_removes_group_deletes_row_and_sends_email(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "fasTimezone": ["UTC"],
                "memberof_group": [],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            expired_at = timezone.now() - datetime.timedelta(days=1)
            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expired_at,
            )

            self.assertTrue(
                Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup")

        remove_mock.assert_called_once()
        self.assertFalse(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())

        from post_office.models import Email

        self.assertTrue(
            Email.objects.filter(
                to="alice@example.com",
                template__name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__membership_type_code="individual",
            ).exists()
        )

    def test_dry_run_does_not_remove_delete_or_email(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "fasTimezone": ["UTC"],
                "memberof_group": [],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            expired_at = timezone.now() - datetime.timedelta(days=1)
            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expired_at,
            )

            self.assertTrue(
                Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup", "--dry-run")

        remove_mock.assert_not_called()
        self.assertTrue(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="alice@example.com",
                template__name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__membership_type_code="individual",
            ).exists()
        )

    def test_command_removes_org_sponsorship_and_sends_email(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        EmailTemplate.objects.filter(
            name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME
        ).delete()
        EmailTemplate.objects.create(
            name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            subject="Your AlmaLinux sponsorship has expired",
            content="Sponsorship for {{ organization_name }} has expired.",
            html_content="<p>Sponsorship for {{ organization_name }} has expired.</p>",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        org = Organization.objects.create(
            name="Acme",
            membership_level_id="gold",
            representative="rep1",
        )
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type=membership_type,
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        rep = FreeIPAUser(
            "rep1",
            {
                "uid": ["rep1"],
                "mail": ["rep1@example.com"],
                "memberof_group": ["almalinux-gold"],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAUser.get", return_value=rep):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup")

        remove_mock.assert_called_once()
        self.assertFalse(OrganizationSponsorship.objects.filter(organization=org).exists())

        org.refresh_from_db()
        self.assertIsNone(org.membership_level_id)

        from post_office.models import Email

        self.assertTrue(
            Email.objects.filter(
                to="rep1@example.com",
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
            ).exists()
        )

    def test_dry_run_does_not_remove_org_sponsorship_or_email(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        EmailTemplate.objects.filter(
            name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME
        ).delete()
        EmailTemplate.objects.create(
            name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            subject="Your AlmaLinux sponsorship has expired",
            content="Sponsorship for {{ organization_name }} has expired.",
            html_content="<p>Sponsorship for {{ organization_name }} has expired.</p>",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        org = Organization.objects.create(
            name="Acme",
            membership_level_id="gold",
            representative="rep1",
        )
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type=membership_type,
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        rep = FreeIPAUser(
            "rep1",
            {
                "uid": ["rep1"],
                "mail": ["rep1@example.com"],
                "memberof_group": ["almalinux-gold"],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAUser.get", return_value=rep):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup", "--dry-run")

        remove_mock.assert_not_called()
        self.assertTrue(OrganizationSponsorship.objects.filter(organization=org).exists())

        org.refresh_from_db()
        self.assertEqual(org.membership_level_id, "gold")

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="rep1@example.com",
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
            ).exists()
        )
