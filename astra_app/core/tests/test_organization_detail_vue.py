import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipRequest, MembershipType, MembershipTypeCategory, Organization
from core.permissions import ASTRA_VIEW_MEMBERSHIP
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP


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
        for permission in (ASTRA_VIEW_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="alice",
            )
        self._login_as_freeipa_user("alice")
        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=user),
            patch("core.views_organizations._build_organization_detail_page_context") as build_context,
        ):
            response = self.client.get(reverse("organization-detail", args=[organization.pk]))

        self.assertEqual(response.status_code, 200)
        build_context.assert_not_called()
        self.assertContains(response, "data-organization-detail-root")
        self.assertContains(
            response,
            f'data-organization-detail-api-url="{reverse("api-organization-detail", args=[organization.pk])}"',
        )
        self.assertContains(response, 'data-organization-detail-membership-request-detail-template="/membership/request/__request_id__/"')
        self.assertContains(response, 'data-organization-detail-user-profile-url-template="/user/__username__/"')
        self.assertContains(response, 'data-organization-detail-send-mail-url-template="/email-tools/send-mail/?type=manual&amp;to=__email__"')
        self.assertContains(
            response,
            f'data-organization-detail-membership-notes-summary-url="{reverse("api-membership-notes-aggregate-summary")}?target_type=org&amp;target={organization.pk}"',
        )
        self.assertContains(
            response,
            f'data-organization-detail-membership-notes-detail-url="{reverse("api-membership-notes-aggregate")}?target_type=org&amp;target={organization.pk}"',
        )
        self.assertContains(
            response,
            f'data-organization-detail-membership-notes-add-url="{reverse("api-membership-notes-aggregate-add")}"',
        )
        self.assertContains(
            response,
            f'data-organization-detail-membership-request-url="{reverse("organization-membership-request", args=[organization.pk])}"',
        )
        self.assertContains(
            response,
            f'data-organization-detail-sponsorship-set-expiry-url-template="{reverse("organization-sponsorship-set-expiry", args=[organization.pk, "__membership_type_code__"])}"',
        )
        self.assertContains(
            response,
            f'data-organization-detail-sponsorship-terminate-url-template="{reverse("organization-sponsorship-terminate", args=[organization.pk, "__membership_type_code__"])}"',
        )
        self.assertContains(response, 'data-organization-detail-csrf-token="')
        self.assertContains(
            response,
            f'data-organization-detail-next-url="{reverse("organization-detail", args=[organization.pk])}"',
        )
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/organizationDetail.ts"')
        self.assertNotContains(response, '<strong>Membership</strong>', html=True)

    def test_organization_detail_api_returns_summary_payload(self) -> None:
        sponsor_type = self._ensure_sponsor_type()
        created_at = datetime(2026, 1, 15, 12, 0, tzinfo=datetime_timezone.utc)
        expires_at = datetime(2026, 9, 26, 12, 0, tzinfo=datetime_timezone.utc)
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
        membership = Membership.objects.create(
            target_organization=organization,
            membership_type=sponsor_type,
            expires_at=expires_at,
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=created_at)
        pending_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type=sponsor_type,
            status=MembershipRequest.Status.pending,
        )

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
        self.assertEqual(payload["organization"]["memberships"][0]["member_since_label"], "January 2026")
        self.assertEqual(payload["organization"]["memberships"][0]["expires_label"], "Sep 26, 2026")
        self.assertEqual(payload["organization"]["memberships"][0]["expires_tone"], "muted")
        self.assertEqual(payload["organization"]["pending_memberships"][0]["request_id"], pending_request.pk)
        self.assertEqual(payload["organization"]["pending_memberships"][0]["badge_label"], "Under review")
        self.assertEqual(payload["organization"]["pending_memberships"][0]["membership_type"]["name"], "Gold Sponsor Member")
        self.assertEqual(payload["organization"]["contact_groups"][0]["label"], "Business")
        self.assertEqual(payload["organization"]["address"]["city"], "Durham")
        self.assertNotIn("detail_url", payload["organization"])
        self.assertNotIn("notes", payload["organization"])
        self.assertNotIn("actions", payload)
        self.assertNotIn("permissions", payload)

    def test_organization_detail_api_includes_sponsorship_management_capabilities(self) -> None:
        for permission in (ASTRA_VIEW_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="alice",
            )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 1,
            },
        )
        gold_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        ruby_type, _ = MembershipType.objects.update_or_create(
            code="ruby",
            defaults={
                "name": "Ruby Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        organization = Organization.objects.create(name="Acme Org", representative="alice")
        membership = Membership.objects.create(
            target_organization=organization,
            membership_type=gold_type,
            expires_at=timezone.now() + timedelta(days=30),
        )

        self._login_as_freeipa_user("alice")
        representative = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "cn": ["Alice Example"], "memberof_group": [], "c": ["US"]},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=representative):
            response = self.client.get(reverse("api-organization-detail", args=[organization.pk]), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        entry = payload["organization"]["memberships"][0]
        self.assertEqual(entry["membership_type"]["code"], gold_type.code)
        self.assertTrue(entry["can_request_tier_change"])
        self.assertEqual(entry["tier_change_membership_type_code"], ruby_type.code)
        self.assertEqual(entry["request_id"], None)
        self.assertTrue(entry["can_manage_expiration"])
        self.assertEqual(entry["expires_on"], membership.expires_at.astimezone(datetime_timezone.utc).strftime("%Y-%m-%d"))
        self.assertNotIn("tier_change_url", entry)
        self.assertNotIn("management", entry)
