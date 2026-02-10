
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import MembershipRequest, MembershipType, Organization


class UserProfileMembershipActionRequiredAlertTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_self_profile_shows_action_required_alert_for_on_hold_request(self) -> None:
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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "givenname": ["Alice"],
                "sn": ["User"],
            },
        )

        self._login_as_freeipa_user("alice")
        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please address the following issues")
        self.assertNotContains(resp, "Please complete these steps to finish setting up your account")
        self.assertContains(resp, 'id="membership-action-required-alert"')
        self.assertContains(resp, "Help us review your membership request")
        self.assertContains(resp, "Add details")
        self.assertContains(resp, reverse("membership-request-self", args=[req.pk]))
        self.assertContains(resp, "alert alert-danger")
        self.assertContains(resp, ">Action required<")

    def test_self_profile_shows_action_required_alert_for_on_hold_org_request(self) -> None:
        MembershipType.objects.update_or_create(
            code="org",
            defaults={
                "name": "Organization",
                "group_cn": "almalinux-org",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Example Org", representative="alice")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="org",
            status=MembershipRequest.Status.on_hold,
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "givenname": ["Alice"],
                "sn": ["User"],
            },
        )

        self._login_as_freeipa_user("alice")
        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please address the following issues")
        self.assertNotContains(resp, "Please complete these steps to finish setting up your account")
        self.assertContains(resp, 'id="sponsorship-action-required-alert"')
        self.assertContains(resp, "Help us review your sponsorship request")
        self.assertContains(resp, "Add details")
        self.assertContains(resp, reverse("membership-request-self", args=[req.pk]))
        self.assertContains(resp, "alert alert-danger")
        self.assertContains(resp, ">Action required<")
        self.assertContains(resp, "Example Org")

    def test_other_profile_keeps_on_hold_badge_label(self) -> None:
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

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "givenname": ["Alice"],
                "sn": ["User"],
            },
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
            return {"alice": alice, "bob": bob}.get(username)

        self._login_as_freeipa_user("bob")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        # Pending membership requests are only visible to the user themself or
        # membership reviewers.
        self.assertNotContains(resp, "On hold")
        self.assertNotContains(resp, "Action required")
