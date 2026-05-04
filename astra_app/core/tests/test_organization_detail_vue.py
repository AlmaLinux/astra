import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from core.freeipa.user import FreeIPAUser
from core.models import (
    FreeIPAPermissionGrant,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP


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
            f'data-organization-detail-api-url="{reverse("api-organization-detail-page", args=[organization.pk])}"',
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

    def test_organization_detail_api_route_is_retired(self) -> None:
        self.assertEqual(reverse("api-organization-detail-page", args=[1]), "/api/v1/organizations/1/detail")

        with self.assertRaises(NoReverseMatch):
            reverse("api-organization-detail", args=[1])

    def test_organization_detail_page_api_returns_data_only_payload(self) -> None:
        sponsor_type = self._ensure_sponsor_type()
        organization = Organization.objects.create(name="Acme Org", representative="alice")
        self._login_as_freeipa_user("alice")

        context = {
            "organization": organization,
            "sponsorships": [],
            "sponsorship_entries": [
                {
                    "sponsorship": {
                        "membership_type": sponsor_type,
                    },
                    "request_id": None,
                    "created_at": "2024-01-15T12:00:00+00:00",
                    "expires_at": "2026-04-30T00:00:00+00:00",
                    "is_expiring_soon": True,
                    "can_request_tier_change": True,
                    "tier_change_membership_type_code": "ruby",
                }
            ],
            "pending_requests": [
                {
                    "request_id": 17,
                    "status": "on_hold",
                    "membership_type": sponsor_type,
                }
            ],
            "representative_username": "alice",
            "representative_full_name": "Alice Example",
            "contact_display_groups": [
                {
                    "key": "business",
                    "label": "Business",
                    "name": "Business Person",
                    "email": "biz@example.com",
                    "phone": "",
                }
            ],
            "is_representative": True,
            "can_request_membership": True,
        }

        with (
            patch("core.views_organizations._build_organization_detail_page_context", return_value=context),
            patch(
                "core.views_organizations.membership_review_permissions",
                return_value={
                    "membership_can_view": True,
                    "membership_can_change": True,
                    "membership_can_delete": True,
                },
            ),
        ):
            response = self.client.get(reverse("api-organization-detail-page", args=[organization.pk]), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["organization"]["is_representative"])
        membership = payload["organization"]["memberships"][0]
        pending_membership = payload["organization"]["pending_memberships"][0]
        self.assertEqual(membership["membership_type"]["code"], sponsor_type.code)
        self.assertEqual(membership["created_at"], "2024-01-15T12:00:00+00:00")
        self.assertEqual(membership["expires_at"], "2026-04-30T00:00:00+00:00")
        self.assertTrue(membership["is_expiring_soon"])
        self.assertNotIn("label", membership)
        self.assertNotIn("class_name", membership)
        self.assertNotIn("member_since_label", membership)
        self.assertNotIn("expires_label", membership)
        self.assertNotIn("expires_tone", membership)
        self.assertEqual(pending_membership["status"], "on_hold")
        self.assertNotIn("badge_label", pending_membership)
        self.assertNotIn("badge_class_name", pending_membership)
        self.assertNotIn("label", payload["organization"]["contact_groups"][0])

    def test_organization_detail_page_api_preserves_non_representative_on_hold_payload(self) -> None:
        sponsor_type = self._ensure_sponsor_type()
        organization = Organization.objects.create(name="Acme Org", representative="alice")
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type=sponsor_type,
            status=MembershipRequest.Status.on_hold,
        )
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="bob",
        )

        self._login_as_freeipa_user("bob")

        with patch(
            "core.freeipa.user.FreeIPAUser.get",
            side_effect=lambda username: FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "cn": ["Alice Example"] if username == "alice" else ["Bob Reviewer"],
                    "memberof_group": [],
                    "c": ["US"],
                },
            ),
        ):
            response = self.client.get(reverse("api-organization-detail-page", args=[organization.pk]), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(payload["organization"]["is_representative"])
        self.assertEqual(payload["organization"]["pending_memberships"], [
            {
                "request_id": payload["organization"]["pending_memberships"][0]["request_id"],
                "status": "on_hold",
                "membership_type": {
                    "name": sponsor_type.name,
                    "code": sponsor_type.code,
                    "description": sponsor_type.description,
                },
            }
        ])

