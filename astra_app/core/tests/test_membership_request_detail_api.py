from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from core.country_codes import country_attr_name
from core.freeipa.user import FreeIPAUser
from core.models import (
    FreeIPAPermissionGrant,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL, ASTRA_VIEW_MEMBERSHIP


class MembershipRequestDetailApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
            },
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP, ASTRA_ADD_SEND_MAIL):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=committee_cn,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_committee_detail_page_is_vue_shell_and_api_returns_structured_payload(self) -> None:
        membership_type = MembershipType.objects.get(code="mirror")
        membership_request = MembershipRequest.objects.create(
            requested_username="bob",
            membership_type=membership_type,
            responses=[
                {"Domain": "mirror.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
            ],
        )
        MembershipLog.objects.create(
            actor_username="alice",
            target_username="bob",
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.requested,
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "givenname": ["Alice"],
                "sn": ["Requester"],
                "memberof_group": [],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "givenname": ["Bob"],
                "sn": ["Target"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str, **_kwargs) -> FreeIPAUser | None:
            return {"reviewer": reviewer, "alice": alice, "bob": bob}.get(username)

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            page_response = self.client.get(reverse("membership-request-detail", args=[membership_request.pk]))
            api_response = self.client.get(reverse("api-membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'data-membership-request-detail-root=""')
        self.assertContains(
            page_response,
            f'data-membership-request-detail-api-url="{reverse("api-membership-request-detail", args=[membership_request.pk])}"',
        )
        self.assertContains(
            page_response,
            'data-membership-request-detail-user-profile-url-template="/user/__username__/"',
        )
        self.assertContains(
            page_response,
            'data-membership-request-detail-organization-detail-url-template="/organization/__organization_id__/"',
        )
        self.assertContains(
            page_response,
            f'data-membership-request-detail-note-summary-url="{reverse("api-membership-request-notes-summary", args=[membership_request.pk])}"',
        )
        self.assertNotContains(page_response, "data-membership-request-notes-root")
        self.assertNotContains(page_response, "data-membership-request-actions-root")
        self.assertNotContains(page_response, "Request responses")

        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.headers["Cache-Control"], "private, no-cache")
        payload = api_response.json()
        self.assertEqual(payload["viewer"]["mode"], "committee")
        self.assertNotIn("title", payload["viewer"])
        self.assertNotIn("back_link", payload["viewer"])
        self.assertNotIn("status_display", payload["request"])
        self.assertTrue(payload["request"]["requested_by"]["show"])
        self.assertTrue(payload["request"]["requested_for"]["show"])
        self.assertNotIn("url", payload["request"]["requested_by"])
        self.assertNotIn("url", payload["request"]["requested_for"])
        self.assertNotIn("notes", payload)
        self.assertNotIn("contact_url", payload["committee"])
        self.assertEqual(payload["committee"]["reopen"], {"show": False})
        self.assertEqual(
            payload["committee"]["actions"],
            {
                "canRequestInfo": True,
                "showOnHoldApprove": False,
            },
        )
        self.assertEqual(
            payload["request"]["responses"][0],
            {
                "question": "Domain",
                "answer_text": "mirror.example.org",
                "segments": [
                    {
                        "kind": "link",
                        "text": "mirror.example.org",
                        "url": "https://mirror.example.org",
                    }
                ],
            },
        )

    def test_detail_page_and_api_return_404_for_unauthorized_viewer(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            responses=[{"Domain": "mirror.example.org"}],
        )

        outsider = FreeIPAUser(
            "mallory",
            {
                "uid": ["mallory"],
                "mail": ["mallory@example.com"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_user("mallory")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=outsider):
            page_response = self.client.get(reverse("membership-request-detail", args=[membership_request.pk]))
            api_response = self.client.get(reverse("api-membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(page_response.status_code, 404)
        self.assertEqual(api_response.status_code, 404)
        self.assertEqual(api_response.headers["Content-Type"], "application/json")
        self.assertEqual(api_response.json(), {"error": "Not found."})

    def test_self_service_detail_api_hides_committee_only_fields_and_skips_committee_payload_assembly(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            status=MembershipRequest.Status.approved,
            decided_by_username="reviewer",
            responses=[{"Domain": "mirror.example.org"}],
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_user("alice")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_membership.user._committee_detail_payload", side_effect=AssertionError("committee payload should not be assembled for self-service viewers")),
        ):
            response = self.client.get(reverse("api-membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["viewer"]["mode"], "self_service")
        self.assertNotIn("committee", payload)
        self.assertNotIn("notes", payload)
        self.assertFalse(payload["request"]["requested_by"]["show"])
        self.assertFalse(payload["request"]["requested_for"]["show"])
        self.assertNotIn("decided_by_username", payload["request"])

    def test_self_service_org_request_keeps_organization_row_only_when_applicable(self) -> None:
        organization = Organization.objects.create(name="Example Org", representative="alice")
        membership_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type_id="mirror",
            responses=[],
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_user("alice")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            response = self.client.get(reverse("api-membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["viewer"]["mode"], "self_service")
        self.assertFalse(payload["request"]["requested_by"]["show"])
        self.assertTrue(payload["request"]["requested_for"]["show"])
        self.assertEqual(payload["request"]["requested_for"]["label"], "Example Org")
        self.assertEqual(payload["request"]["requested_for"]["kind"], "organization")
        self.assertEqual(payload["request"]["requested_for"]["organization_id"], organization.pk)
        self.assertNotIn("url", payload["request"]["requested_for"])

    def test_self_service_detail_api_omits_route_and_display_fields(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            status=MembershipRequest.Status.on_hold,
            responses=[{"Domain": "mirror.example.org"}],
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_user("alice")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            response = self.client.get(reverse("api-membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("title", payload["viewer"])
        self.assertNotIn("back_link", payload["viewer"])
        self.assertNotIn("status_display", payload["request"])
        self.assertNotIn("url", payload["request"]["requested_by"])
        self.assertNotIn("url", payload["request"]["requested_for"])
        self.assertNotIn("rescind_url", payload["self_service"])
        self.assertIsNotNone(payload["self_service"]["form"])
        self.assertNotIn("action_url", payload["self_service"]["form"])
        self.assertNotIn("submit_label", payload["self_service"]["form"])

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["IR"])
    def test_committee_detail_api_includes_embargo_compliance_warning(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            responses=[],
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type_id="mirror",
            membership_request=membership_request,
            requested_group_cn="almalinux-mirror",
            action=MembershipLog.Action.requested,
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "givenname": ["Alice"],
                "sn": ["User"],
                country_attr_name(): ["IR"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str, **_kwargs) -> FreeIPAUser | None:
            return {"reviewer": reviewer, "alice": alice}.get(username)

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            response = self.client.get(reverse("api-membership-request-detail", args=[membership_request.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["committee"]["compliance_warning"],
            {
                "country_code": "IR",
                "country_label": "Iran (IR)",
                "message": "This user's declared country, Iran (IR), is on the list of embargoed countries.",
            },
        )