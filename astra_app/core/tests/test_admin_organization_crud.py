
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.tests.utils_test_data import ensure_core_categories


class AdminOrganizationCRUDTests(TestCase):
    def setUp(self) -> None:
        ensure_core_categories()

    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_can_create_organization_with_representatives_and_is_logged(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        rep_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user, rep_user]),
        ):
            url = reverse("admin:core_organization_add")
            resp = self.client.post(
                url,
                data={
                    "name": "AlmaLinux",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@almalinux.org",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@almalinux.org",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@almalinux.org",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "notes": "Internal notes",
                    "representative": "bob",
                    # MembershipInline management form (no inline rows).
                    "memberships-TOTAL_FORMS": "0",
                    "memberships-INITIAL_FORMS": "0",
                    "memberships-MIN_NUM_FORMS": "0",
                    "memberships-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        org = Organization.objects.get(name="AlmaLinux")
        self.assertEqual(org.name, "AlmaLinux")
        self.assertEqual(org.representative, "bob")

        ContentType.objects.clear_cache()
        ContentType.objects.get_for_model(Organization)

        from django.contrib.auth import get_user_model

        shadow_user = get_user_model().objects.get(username="alice")
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, shadow_user.pk)
        self.assertEqual(entry.action_flag, ADDITION)
        self.assertEqual(entry.object_id, str(org.pk))

    def test_admin_can_create_unclaimed_organization_without_representative(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user]),
        ):
            response = self.client.post(
                reverse("admin:core_organization_add"),
                data={
                    "name": "Admin Unclaimed Org",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@example.org",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@example.org",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@example.org",
                    "website_logo": "https://example.org/logo-options",
                    "website": "https://example.org/",
                    "notes": "Internal notes",
                    "status": Organization.Status.unclaimed,
                    "representative": "",
                    # MembershipInline management form (no inline rows).
                    "memberships-TOTAL_FORMS": "0",
                    "memberships-INITIAL_FORMS": "0",
                    "memberships-MIN_NUM_FORMS": "0",
                    "memberships-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        organization = Organization.objects.get(name="Admin Unclaimed Org")
        self.assertEqual(organization.representative, "")
        self.assertEqual(organization.status, Organization.Status.unclaimed)
        self.assertTrue(organization.claim_secret)

    def test_admin_add_form_hides_legacy_additional_information_field(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            resp = self.client.get(reverse("admin:core_organization_add"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="id_additional_information"')

    def test_admin_generate_claim_url_action_for_unclaimed_org(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            organization = Organization.objects.create(name="Unclaimed Org")

            response = self.client.post(
                reverse("admin:core_organization_changelist"),
                data={
                    "action": "generate_claim_url",
                    "_selected_action": [str(organization.pk)],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/organizations/claim/")
