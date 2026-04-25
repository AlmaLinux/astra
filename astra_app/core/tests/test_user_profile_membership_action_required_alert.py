
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
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
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["accountSetup"]["requiredIsRfi"])
        required_actions = {action["id"]: action for action in payload["accountSetup"]["requiredActions"]}
        action = required_actions["membership-action-required-alert"]
        self.assertEqual(action["label"], "Help us review your membership request")
        self.assertEqual(action["urlLabel"], "Add details")
        self.assertEqual(action["url"], reverse("membership-request-self", args=[req.pk]))
        self.assertEqual(payload["membership"]["pendingEntries"][0]["badge"]["label"], "Action required")

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
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["accountSetup"]["requiredIsRfi"])
        required_actions = {action["id"]: action for action in payload["accountSetup"]["requiredActions"]}
        action = required_actions["sponsorship-action-required-alert"]
        self.assertEqual(action["label"], "Help us review your sponsorship request")
        self.assertEqual(action["urlLabel"], "Add details")
        self.assertEqual(action["url"], reverse("membership-request-self", args=[req.pk]))
        self.assertEqual(payload["membership"]["pendingEntries"][0]["organizationName"], "Example Org")
        self.assertEqual(payload["membership"]["pendingEntries"][0]["badge"]["label"], "Action required")

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
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        # Pending membership requests are only visible to the user themself or
        # membership reviewers.
        payload = resp.json()
        self.assertEqual(payload["accountSetup"]["requiredActions"], [])
        self.assertEqual(payload["membership"]["pendingEntries"], [])
