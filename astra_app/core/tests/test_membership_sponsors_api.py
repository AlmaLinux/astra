import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType, Organization
from core.permissions import ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipSponsorsApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        MembershipType.objects.update_or_create(
            code="sponsor-standard",
            defaults={
                "name": "Sponsor Standard",
                "group_cn": "sponsor-standard",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="individual-standard",
            defaults={
                "name": "Individual Standard",
                "group_cn": "individual-standard",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_membership_permission(self) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _reviewer_user(self, *, groups: list[str] | None = None) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "displayname": ["Reviewer User"],
                "memberof_group": list(groups or []),
            },
        )

    def _datatables_query(self, *, start: int = 0, length: int = 25) -> dict[str, str]:
        return {
            "draw": "3",
            "start": str(start),
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "order[0][name]": "expires_at",
            "columns[0][data]": "membership_id",
            "columns[0][name]": "expires_at",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

    def test_membership_sponsors_api_requires_permission(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user(groups=[])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-sponsors"),
                data=self._datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Permission denied."})

    def test_membership_sponsors_api_returns_datatables_payload_and_sponsor_rows(self) -> None:
        self._grant_membership_permission()
        self._login_as_freeipa_user("reviewer")

        sponsor_org = Organization.objects.create(name="Sponsor Org", representative="repuser")
        fallback_org = Organization.objects.create(name="Fallback Org", representative="repfallback")

        Membership.objects.create(
            target_organization=sponsor_org,
            membership_type_id="sponsor-standard",
            expires_at=timezone.now() + datetime.timedelta(days=5, minutes=1),
        )
        Membership.objects.create(
            target_organization=fallback_org,
            membership_type_id="sponsor-standard",
            expires_at=None,
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="individual-standard",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        reviewer = self._reviewer_user(groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        sponsor_rep = FreeIPAUser(
            "repuser",
            {
                "uid": ["repuser"],
                "displayname": ["Representative User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "repuser":
                return sponsor_rep
            if username == "repfallback":
                raise RuntimeError("FreeIPA unavailable")
            return None

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=[]):
            with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
                response = self.client.get(
                    reverse("api-membership-sponsors"),
                    data=self._datatables_query(),
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["draw"], 3)
        self.assertEqual(payload["recordsTotal"], 2)
        self.assertEqual(payload["recordsFiltered"], 2)
        self.assertEqual(len(payload["data"]), 2)

        first = payload["data"][0]
        second = payload["data"][1]

        self.assertEqual(first["organization"]["name"], "Sponsor Org")
        self.assertEqual(first["representative"]["username"], "repuser")
        self.assertEqual(first["representative"]["display_label"], "Representative User (repuser)")
        self.assertEqual(first["sponsorship_level"], "Sponsor Standard")
        self.assertTrue(first["is_expiring_soon"])
        self.assertIn("days left", first["expires_display"])
        self.assertNotIn("url", first["organization"])
        self.assertNotIn("url", first["representative"])

        self.assertEqual(second["organization"]["name"], "Fallback Org")
        self.assertEqual(second["representative"]["username"], "repfallback")
        self.assertEqual(second["representative"]["display_label"], "repfallback")
        self.assertEqual(second["expires_display"], "-")
        self.assertEqual(second["expires_at_order"], "9999-12-31")

    def test_membership_sponsors_api_rejects_invalid_query_parameters(self) -> None:
        self._grant_membership_permission()
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user(groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(
                reverse("api-membership-sponsors"),
                data={**self._datatables_query(), "unexpected": "1"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid query parameters."})

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
    def test_membership_sponsors_page_renders_vue_shell_contract(self) -> None:
        self._grant_membership_permission()
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user(groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(f"{reverse('membership-sponsors')}?q=sponsor")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-membership-sponsors-root")
        self.assertContains(response, 'data-membership-sponsors-api-url="/api/v1/membership/sponsors"')
        self.assertContains(response, 'data-membership-sponsors-page-size="25"')
        self.assertContains(response, 'data-membership-sponsors-initial-q="sponsor"')
        self.assertContains(response, 'data-membership-sponsors-organization-detail-url-template="/organization/__organization_id__/"')
        self.assertContains(response, 'data-membership-sponsors-user-profile-url-template="/user/__username__/"')
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/membershipSponsors.ts"')
