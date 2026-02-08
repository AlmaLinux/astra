
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class AdminOrganizationCRUDTests(TestCase):
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
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        rep_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
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
                    "membership_level": "silver",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "additional_information": "",
                    "notes": "Internal notes",
                    "representative": "bob",
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
