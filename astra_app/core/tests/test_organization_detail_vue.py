import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import Membership, MembershipType, MembershipTypeCategory, Organization


class OrganizationDetailVueTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _ensure_sponsor_type(self) -> MembershipType:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 1,
            },
        )
        sponsor_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        return sponsor_type

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_organization_detail_page_renders_vue_shell_contract(self) -> None:
        organization = Organization.objects.create(name="Acme Org", representative="alice")
        self._login_as_freeipa_user("alice")
        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            response = self.client.get(reverse("organization-detail", args=[organization.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-organization-detail-root")
        self.assertContains(
            response,
            f'data-organization-detail-api-url="{reverse("api-organization-detail", args=[organization.pk])}"',
        )
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/organizationDetail.ts"')
        self.assertContains(response, '<strong>Membership</strong>', html=True)

    def test_organization_detail_api_returns_summary_payload(self) -> None:
        sponsor_type = self._ensure_sponsor_type()
        organization = Organization.objects.create(
            name="Acme Org",
            representative="alice",
            website="https://example.com",
            business_contact_name="Business Person",
            business_contact_email="biz@example.com",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.com",
            city="Durham",
            country_code="US",
        )
        Membership.objects.create(target_organization=organization, membership_type=sponsor_type)

        self._login_as_freeipa_user("alice")
        representative = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "cn": ["Alice Example"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=representative):
            response = self.client.get(reverse("api-organization-detail", args=[organization.pk]), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["organization"]["name"], "Acme Org")
        self.assertEqual(payload["organization"]["website"], "https://example.com")
        self.assertEqual(payload["organization"]["representative"]["username"], "alice")
        self.assertEqual(payload["organization"]["representative"]["full_name"], "Alice Example")
        self.assertEqual(payload["organization"]["memberships"][0]["label"], "Gold Sponsor Member")
        self.assertEqual(payload["organization"]["contact_groups"][0]["label"], "Business")
        self.assertEqual(payload["organization"]["address"]["city"], "Durham")
        self.assertNotIn("actions", payload)
        self.assertNotIn("permissions", payload)
