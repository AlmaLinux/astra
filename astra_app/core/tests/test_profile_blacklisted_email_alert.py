
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class ProfileBlacklistedEmailAlertTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_blacklisted_email_alert_shown_only_to_self(self) -> None:
        from django_ses.models import BlacklistedEmail

        blacklisted = "bob@example.org"
        BlacklistedEmail.objects.create(email=blacklisted)

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["Builder"],
                "mail": [blacklisted],
            },
        )
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "givenname": ["View"],
                "sn": ["Er"],
                "mail": ["viewer@example.org"],
            },
        )

        def fake_get(username: str, *args: object, **kwargs: object) -> FreeIPAUser | None:
            if username == "bob":
                return bob
            if username == "viewer":
                return viewer
            return None

        # Self view: should see the alert + link.
        self._login_as_freeipa("bob")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_get),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        required_actions = {action["id"]: action for action in resp.json()["accountSetup"]["requiredActions"]}
        action = required_actions["email-blacklisted-alert"]
        self.assertIn("your address may have bounced or been marked as spam", action["label"])
        self.assertEqual(action["urlLabel"], "Update your email address")
        self.assertNotIn("url", action)

        # Other user view: should not see the alert.
        self._login_as_freeipa("viewer")
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_get),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = self.client.get(reverse("api-user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["accountSetup"]["requiredActions"], [])
