
import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.models import (
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    Organization,
)
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class MembershipGroupSyncLifecycleTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        ensure_email_templates()

        for perm in (ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_terminate_removes_user_from_group(self) -> None:
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

        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual",
            category_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                resp = self.client.post(
                    reverse("membership-terminate", args=["alice", "individual"]),
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        remove_mock.assert_called_once()
        _, remove_kwargs = remove_mock.call_args
        self.assertEqual(remove_kwargs["group_name"], "almalinux-individual")

    def test_org_sponsorship_terminate_removes_representative_from_group(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["almalinux-gold"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                resp = self.client.post(
                    reverse("organization-sponsorship-terminate", args=[org.pk, "gold"]),
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        remove_mock.assert_called_once()
        _, remove_kwargs = remove_mock.call_args
        self.assertEqual(remove_kwargs["group_name"], "almalinux-gold")

    def test_org_representative_change_transfers_sponsorship_group(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["almalinux-gold"],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with (
                patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock,
                patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
            ):
                resp = self.client.post(
                    reverse("organization-edit", args=[org.pk]),
                    data={
                        "name": org.name,
                        "country_code": "US",
                        "business_contact_name": "Biz",
                        "business_contact_email": "biz@example.com",
                        "pr_marketing_contact_name": "PR",
                        "pr_marketing_contact_email": "pr@example.com",
                        "technical_contact_name": "Tech",
                        "technical_contact_email": "tech@example.com",
                        "website_logo": "https://example.com/logo.png",
                        "website": "https://example.com",
                        "representative": "alice",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        remove_mock.assert_called_once()
        _, remove_kwargs = remove_mock.call_args
        self.assertEqual(remove_kwargs["group_name"], "almalinux-gold")
        add_mock.assert_called_once()
        _, add_kwargs = add_mock.call_args
        self.assertEqual(add_kwargs["group_name"], "almalinux-gold")

    def test_org_sponsorship_expired_cleanup_removes_representative_and_clears_level(self) -> None:
        MembershipType.objects.update_or_create(
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
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["almalinux-gold"],
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

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=bob):
                with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                    call_command("membership_expired_cleanup")

        remove_mock.assert_called_once()
        org.refresh_from_db()
        self.assertFalse(Membership.objects.filter(target_organization=org).exists())

    def test_org_sponsorship_expired_cleanup_does_not_clear_db_when_freeipa_removal_fails(self) -> None:
        MembershipType.objects.update_or_create(
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
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=frozen_now - datetime.timedelta(days=1),
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=None):
                call_command("membership_expired_cleanup")

        org.refresh_from_db()
        self.assertTrue(Membership.objects.filter(target_organization=org).exists())
