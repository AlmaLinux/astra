
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAFASAgreement, FreeIPAUser
from core.models import MembershipRequest, MembershipType


class ProfileAccountSetupAlertTests(TestCase):
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
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.backends.FreeIPAFASAgreement.all", return_value=[agreement]),
            patch("core.backends.FreeIPAFASAgreement.get", return_value=agreement),
            patch(
                "core.views_users.country_code_status_from_user_data",
                return_value=SimpleNamespace(code="US", is_valid=True),
            ),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="account-setup-required-alert"')
        self.assertContains(resp, "Please complete these steps to finish setting up your account")
        self.assertNotContains(resp, "Please address the following issues")
        self.assertContains(resp, coc_cn)
        settings_url = f"{reverse('settings')}?agreement={quote(coc_cn)}#agreements"
        self.assertContains(resp, f'href="{settings_url}"')

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
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.backends.FreeIPAFASAgreement.all", return_value=[]),
            patch(
                "core.views_users.country_code_status_from_user_data",
                return_value=SimpleNamespace(code="US", is_valid=True),
            ),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="account-setup-recommended-alert"')
        self.assertContains(resp, f'href="{reverse("membership-request")}"')

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
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.backends.FreeIPAFASAgreement.all", return_value=[]),
            patch(
                "core.views_users.country_code_status_from_user_data",
                return_value=SimpleNamespace(code="US", is_valid=True),
            ),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="account-setup-recommended-alert"')
