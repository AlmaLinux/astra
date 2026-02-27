
import datetime
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.membership import FreeIPAGroupRemovalOutcome
from core.membership_log_side_effects import apply_membership_log_side_effects
from core.models import Membership, MembershipLog, MembershipType, MembershipTypeCategory, Organization
from core.public_urls import normalize_public_base_url


class MembershipExpiredCleanupCommandTests(TestCase):
    def setUp(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_organization": True,
                "sort_order": 1,
            },
        )

        EmailTemplate.objects.filter(
            name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME
        ).delete()
        EmailTemplate.objects.create(
            name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            subject="Membership expired",
            content="Membership expired",
            html_content="<p>Membership expired</p>",
        )
        EmailTemplate.objects.filter(
            name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME
        ).delete()
        EmailTemplate.objects.create(
            name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            subject="Sponsorship expired",
            content="Sponsorship expired",
            html_content="<p>Sponsorship expired</p>",
        )

    def _create_membership_log_with_side_effects(self, **kwargs) -> MembershipLog:
        log = MembershipLog.objects.create(**kwargs)
        apply_membership_log_side_effects(log=log)
        return log

    def test_command_removes_group_deletes_row_and_sends_email(self) -> None:
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
            self._create_membership_log_with_side_effects(
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

            with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
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
                "category_id": "individual",
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
            self._create_membership_log_with_side_effects(
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

            with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
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
                "category_id": "sponsorship",
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
            representative="rep1",
        )
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type,
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        rep = FreeIPAUser(
            "rep1",
            {
                "uid": ["rep1"],
                "mail": ["rep1@example.com"],
                "fasTimezone": ["America/New_York"],
                "memberof_group": ["almalinux-gold"],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=rep):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup")

        remove_mock.assert_called_once()
        self.assertFalse(Membership.objects.filter(target_organization=org).exists())

        from post_office.models import Email

        request_path = reverse("organization-membership-request", kwargs={"organization_id": org.pk})
        base = normalize_public_base_url(settings.PUBLIC_BASE_URL)
        expected_extend_url = f"{base}{request_path}?membership_type=gold" if base else f"{request_path}?membership_type=gold"

        self.assertTrue(
            Email.objects.filter(
                to="rep1@example.com",
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
                context__extend_url=expected_extend_url,
            ).exists()
        )

        email = Email.objects.filter(
            to="rep1@example.com",
            template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
        ).latest("created")
        ctx = dict(email.context or {})
        self.assertTrue(str(ctx.get("expires_at") or "").endswith("(America/New_York)"))
        self.assertEqual(email.cc, [settings.MEMBERSHIP_COMMITTEE_EMAIL])
        self.assertEqual((email.headers or {}).get("Reply-To"), settings.MEMBERSHIP_COMMITTEE_EMAIL)

    def test_dry_run_does_not_remove_org_sponsorship_or_email(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
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
            representative="rep1",
        )
        Membership.objects.create(
            target_organization=org,
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
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=rep):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup", "--dry-run")

        remove_mock.assert_not_called()
        self.assertTrue(Membership.objects.filter(target_organization=org).exists())

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="rep1@example.com",
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
            ).exists()
        )

    def test_command_skips_user_cleanup_when_freeipa_removal_fails(self) -> None:
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
            self._create_membership_log_with_side_effects(
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

            with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
                with patch(
                    "core.management.commands.membership_expired_cleanup.remove_user_from_group",
                    return_value=False,
                    create=True,
                ):
                    call_command("membership_expired_cleanup")

        self.assertTrue(Membership.objects.filter(target_username="alice", membership_type_id="individual").exists())

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="alice@example.com",
                template__name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__membership_type_code="individual",
            ).exists()
        )

    def test_command_skips_org_cleanup_when_freeipa_removal_fails(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
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
            name="Fail Org",
            representative="rep1",
        )
        Membership.objects.create(
            target_organization=org,
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
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=rep):
                with patch(
                    "core.management.commands.membership_expired_cleanup.remove_organization_representative_from_group_if_present",
                    return_value=FreeIPAGroupRemovalOutcome.failed,
                    create=True,
                ):
                    call_command("membership_expired_cleanup")

        self.assertTrue(Membership.objects.filter(target_organization=org).exists())

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="rep1@example.com",
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
            ).exists()
        )

    def test_command_falls_back_to_primary_contact_when_representative_email_missing(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        org = Organization.objects.create(
            name="No Rep Email Org",
            representative="rep-no-email",
            business_contact_email="fallback@example.com",
        )
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type,
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        rep = FreeIPAUser(
            "rep-no-email",
            {
                "uid": ["rep-no-email"],
                "mail": [""],
                "memberof_group": ["almalinux-gold"],
            },
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            stderr = StringIO()
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=rep):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True):
                    call_command("membership_expired_cleanup", stderr=stderr)

        from post_office.models import Email

        self.assertTrue(
            Email.objects.filter(
                to="fallback@example.com",
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
            ).exists()
        )
        self.assertEqual("", stderr.getvalue().strip())

    def test_command_warns_and_continues_when_recipient_lookup_fails_and_no_fallback(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        org = Organization.objects.create(
            name="No Contact Org",
            representative="rep-no-email",
            business_contact_email="",
            pr_marketing_contact_email="",
            technical_contact_email="",
        )
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type,
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        stderr = StringIO()
        with patch("django.utils.timezone.now", return_value=frozen_now):
            with (
                patch(
                    "core.management.commands.membership_expired_cleanup.organization_sponsor_email_context",
                    return_value={},
                ),
                patch("core.freeipa.user.FreeIPAUser.get", side_effect=RuntimeError("ipa down")),
            ):
                call_command("membership_expired_cleanup", stderr=stderr)

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                context__organization_id=org.pk,
                context__membership_type_code="gold",
            ).exists()
        )
        self.assertIn("No recipient resolved for organization id=", stderr.getvalue())
        self.assertIn("organization sponsorship expired-cleanup", stderr.getvalue())
