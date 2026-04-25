
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.user import FreeIPAUser
from core.models import MembershipRequest, MembershipType, MembershipTypeCategory


class ProfileAccountSetupAlertTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )

    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_shows_coc_required_action_when_not_signed(self) -> None:
        coc_cn = "AlmaLinux Community Code of Conduct"

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["Builder"],
                "mail": ["bob@example.org"],
            },
        )

        agreement = FreeIPAFASAgreement(
            coc_cn,
            {
                "cn": [coc_cn],
                "description": ["Some CoC text"],
                "ipaenabledflag": ["TRUE"],
                "memberuser": [],
            },
        )

        self._login_as_freeipa("bob")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.all", return_value=[agreement]),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.get", return_value=agreement),
            patch(
                "core.views_users.country_code_status_from_user_data",
                return_value=SimpleNamespace(code="US", is_valid=True),
            ),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        account_setup = resp.json()["accountSetup"]
        self.assertFalse(account_setup["requiredIsRfi"])
        self.assertEqual(account_setup["requiredActions"][0]["id"], "coc-not-signed-alert")
        self.assertEqual(account_setup["requiredActions"][0]["label"], f"Sign the {coc_cn}")
        self.assertEqual(
            account_setup["requiredActions"][0]["url"],
            f'{reverse("settings")}?tab=agreements&agreement=AlmaLinux+Community+Code+of+Conduct&return=profile',
        )

    def test_shows_recommended_membership_request_when_no_individual_membership(self) -> None:
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["Builder"],
                "mail": ["bob@example.org"],
            },
        )

        MembershipType.objects.get_or_create(
            code="individual_test",
            defaults={
                "name": "Individual",
                "votes": 1,
                "category_id": "individual",
                "enabled": True,
                "group_cn": "some-group",
            },
        )

        self._login_as_freeipa("bob")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.all", return_value=[]),
            patch(
                "core.views_users.country_code_status_from_user_data",
                return_value=SimpleNamespace(code="US", is_valid=True),
            ),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        recommended = resp.json()["accountSetup"]["recommendedActions"]
        self.assertEqual(recommended[0]["id"], "membership-request-recommended-alert")
        self.assertEqual(recommended[0]["url"], reverse("membership-request"))

    def test_does_not_recommend_membership_when_request_already_pending(self) -> None:
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["Builder"],
                "mail": ["bob@example.org"],
            },
        )

        membership_type, _created = MembershipType.objects.get_or_create(
            code="individual_test_pending",
            defaults={
                "name": "Individual",
                "votes": 1,
                "category_id": "individual",
                "enabled": True,
                "group_cn": "some-group",
            },
        )

        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )

        self._login_as_freeipa("bob")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.all", return_value=[]),
            patch(
                "core.views_users.country_code_status_from_user_data",
                return_value=SimpleNamespace(code="US", is_valid=True),
            ),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["accountSetup"]["recommendedActions"], [])
