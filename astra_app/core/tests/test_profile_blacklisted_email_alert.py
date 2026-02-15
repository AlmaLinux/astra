
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


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
        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="account-setup-required-alert"')
        self.assertContains(resp, 'id="email-blacklisted-alert"')
        self.assertContains(resp, "blacklisted", html=False)
        self.assertContains(resp, f'href="{reverse("settings")}?tab=emails"')

        # Other user view: should not see the alert.
        self._login_as_freeipa("viewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "bob"}))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="email-blacklisted-alert"')
        self.assertNotContains(resp, "blacklisted", html=False)
