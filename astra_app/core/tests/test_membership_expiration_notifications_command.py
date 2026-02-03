from __future__ import annotations

import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import (
    Membership,
    MembershipLog,
    MembershipType,
    Organization,
    OrganizationSponsorship,
)


class MembershipExpirationNotificationsCommandTests(TestCase):
    def test_command_sends_expiring_soon_email_on_schedule(self) -> None:
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
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS // 2
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                call_command("membership_expiration_notifications")

        from post_office.models import Email

        self.assertTrue(
            Email.objects.filter(
                to="alice@example.com",
                template__name=settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
                context__membership_type_code="individual",
            ).exists()
        )

        email = Email.objects.filter(
            to="alice@example.com",
            template__name=settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
        ).latest("created")
        ctx = dict(email.context or {})
        self.assertIn("first_name", ctx)
        self.assertIn("last_name", ctx)
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)

    def test_dry_run_does_not_queue_expiring_soon_email(self) -> None:
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
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS // 2
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                call_command("membership_expiration_notifications", "--dry-run")

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="alice@example.com",
                template__name=settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
                context__membership_type_code="individual",
            ).exists()
        )

    def test_command_does_not_send_expired_email_for_expired_memberships(self) -> None:
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
                "memberof_group": [],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_on_utc = today_utc - datetime.timedelta(days=1)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                call_command("membership_expiration_notifications")

        from post_office.models import Email

        self.assertTrue(
            Membership.objects.filter(target_username="alice", membership_type_id="individual").exists()
        )
        self.assertFalse(
            Email.objects.filter(
                to="alice@example.com",
                template__name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__membership_type_code="individual",
            ).exists()
        )

    def test_command_does_not_send_twice_same_day_without_force(self) -> None:
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
                "memberof_group": [],
            },
        )

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                call_command("membership_expiration_notifications")
                first_count = Email.objects.count()
                call_command("membership_expiration_notifications")
                second_count = Email.objects.count()

        self.assertEqual(first_count, second_count)

    def test_force_sends_even_if_already_sent_today(self) -> None:
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
                "memberof_group": [],
            },
        )

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            MembershipLog.objects.create(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                requested_group_cn="almalinux-individual",
                action=MembershipLog.Action.approved,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                call_command("membership_expiration_notifications")
                first_count = Email.objects.count()
                call_command("membership_expiration_notifications", "--force")
                second_count = Email.objects.count()

        self.assertEqual(first_count + 1, second_count)

    def test_command_sends_org_sponsorship_expiring_soon_email_with_cc(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="sponsor",
            defaults={
                "name": "Sponsor",
                "group_cn": "almalinux-sponsor",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        organization = Organization.objects.create(
            name="Example Org",
            representative="org-rep",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        rep_user = FreeIPAUser(
            "org-rep",
            {
                "uid": ["org-rep"],
                "givenname": ["Org"],
                "sn": ["Rep"],
                "mail": ["rep@example.com"],
                "memberof_group": [],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS // 2
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            OrganizationSponsorship.objects.create(
                organization=organization,
                membership_type=membership_type,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=rep_user):
                call_command("membership_expiration_notifications")

        from post_office.models import Email

        email = Email.objects.filter(
            to="rep@example.com",
            template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
        ).latest("created")

        ctx = dict(email.context or {})
        self.assertEqual(ctx.get("organization_id"), organization.id)
        self.assertEqual(ctx.get("membership_type_code"), membership_type.code)
        self.assertEqual(email.cc, [settings.MEMBERSHIP_COMMITTEE_EMAIL])
        self.assertIn("/organization/", ctx.get("extend_url", ""))

    def test_command_dedupes_org_sponsorship_warnings_without_force(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="sponsor",
            defaults={
                "name": "Sponsor",
                "group_cn": "almalinux-sponsor",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        organization = Organization.objects.create(
            name="Example Org",
            representative="org-rep",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        rep_user = FreeIPAUser(
            "org-rep",
            {
                "uid": ["org-rep"],
                "mail": ["rep@example.com"],
                "memberof_group": [],
            },
        )

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            OrganizationSponsorship.objects.create(
                organization=organization,
                membership_type=membership_type,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=rep_user):
                call_command("membership_expiration_notifications")
                first_count = Email.objects.count()
                call_command("membership_expiration_notifications")
                second_count = Email.objects.count()

        self.assertEqual(first_count, second_count)

    def test_command_force_sends_org_sponsorship_warning_again(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="sponsor",
            defaults={
                "name": "Sponsor",
                "group_cn": "almalinux-sponsor",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        organization = Organization.objects.create(
            name="Example Org",
            representative="org-rep",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        rep_user = FreeIPAUser(
            "org-rep",
            {
                "uid": ["org-rep"],
                "mail": ["rep@example.com"],
                "memberof_group": [],
            },
        )

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            today_utc = timezone.now().astimezone(datetime.UTC).date()
            expires_in_days = settings.MEMBERSHIP_EXPIRING_SOON_DAYS
            expires_on_utc = today_utc + datetime.timedelta(days=expires_in_days)
            expires_at_utc = datetime.datetime.combine(
                expires_on_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC
            )

            OrganizationSponsorship.objects.create(
                organization=organization,
                membership_type=membership_type,
                expires_at=expires_at_utc,
            )

            with patch("core.backends.FreeIPAUser.get", return_value=rep_user):
                call_command("membership_expiration_notifications")
                first_count = Email.objects.count()
                call_command("membership_expiration_notifications", "--force")
                second_count = Email.objects.count()

        self.assertEqual(first_count + 1, second_count)
