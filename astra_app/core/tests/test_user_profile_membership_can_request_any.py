
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import Membership, MembershipRequest, MembershipType, MembershipTypeCategory


class UserProfileMembershipCanRequestAnyTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={"is_organization": True, "sort_order": 1},
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_can_request_any_false_when_all_eligible_types_have_open_requests(self) -> None:
        # Ensure the test is deterministic: only two membership types are requestable.
        MembershipType.objects.update(enabled=False)

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(
            requested_username="alex",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="alex",
            membership_type_id="mirror",
            status=MembershipRequest.Status.on_hold,
        )

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
            },
        )

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["membership"]["canRequestAny"])

    def test_user_profile_does_not_recommend_request_after_rejection(self) -> None:
        # Ensure the test is deterministic: only one membership type is requestable.
        MembershipType.objects.update(enabled=False)

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

        # The user previously opened a request and it was rejected.
        MembershipRequest.objects.create(
            requested_username="alex",
            membership_type_id="individual",
            status=MembershipRequest.Status.rejected,
        )

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
                "c": ["US"],
            },
        )

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        recommended = resp.json()["accountSetup"]["recommendedActions"]
        self.assertFalse(
            any(a.get("id") == "membership-request-recommended-alert" for a in recommended),
            "Did not expect a membership request recommendation after a rejected request.",
        )

    def test_membership_can_request_any_uses_effective_category_when_denorm_drifts(self) -> None:
        MembershipType.objects.update(enabled=False)

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
            target_username="alex",
            membership_type_id="individual",
        )

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
                "c": ["US"],
            },
        )

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["membership"]["canRequestAny"])

    def test_user_profile_required_actions_use_context_aware_settings_links(self) -> None:
        MembershipType.objects.update(enabled=False)

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
            },
        )

        agreement = type("Agreement", (), {"cn": "coc", "enabled": True, "signed": False})

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=True),
            patch("core.views_users.list_agreements_for_user", return_value=[agreement]),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        required_urls = {action["url"] for action in resp.json()["accountSetup"]["requiredActions"]}
        self.assertIn(
            "/settings/?tab=agreements&agreement=AlmaLinux+Community+Code+of+Conduct&return=profile",
            required_urls,
        )
        self.assertIn("/settings/?tab=profile&highlight=country_code", required_urls)

    def test_committee_viewer_sees_country_row_first_with_human_readable_name(self) -> None:
        alex_data = {
            "uid": ["alex"],
            "mail": ["alex@example.com"],
            "memberof_group": [],
            "givenname": ["Alex"],
            "sn": ["User"],
            "fasPronoun": ["they/them"],
        }
        alex_data[settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR] = ["US"]
        alex = FreeIPAUser(
            "alex",
            alex_data,
        )
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
                "givenname": ["Review"],
                "sn": ["Er"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"alex": alex, "reviewer": reviewer}.get(username)

        self._login_as_freeipa_user("reviewer")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["summary"]["viewerIsMembershipCommittee"])
        self.assertEqual(payload["summary"]["profileCountry"], "United States")

    def test_committee_viewer_sees_not_provided_when_country_missing(self) -> None:
        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
            },
        )
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
                "givenname": ["Review"],
                "sn": ["Er"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"alex": alex, "reviewer": reviewer}.get(username)

        self._login_as_freeipa_user("reviewer")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["summary"]["viewerIsMembershipCommittee"])
        self.assertEqual(payload["summary"]["profileCountry"], "Not provided")

    def test_non_committee_viewer_does_not_see_country_row(self) -> None:
        alex_data = {
            "uid": ["alex"],
            "mail": ["alex@example.com"],
            "memberof_group": [],
            "givenname": ["Alex"],
            "sn": ["User"],
            "fasPronoun": ["they/them"],
        }
        alex_data[settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR] = ["US"]
        alex = FreeIPAUser(
            "alex",
            alex_data,
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
                "givenname": ["Bob"],
                "sn": ["Viewer"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"alex": alex, "bob": bob}.get(username)

        self._login_as_freeipa_user("bob")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["summary"]["viewerIsMembershipCommittee"])
        self.assertEqual(payload["summary"]["pronouns"], "they/them")
        self.assertEqual(payload["summary"]["profileCountry"], "")
