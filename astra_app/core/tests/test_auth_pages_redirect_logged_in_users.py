from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class AuthPagesRedirectLoggedInUsersTests(TestCase):
    def _login_as_freeipa(self, client: Client, username: str) -> None:
        session = client.session
        session["_freeipa_username"] = username
        session.save()

    def _make_freeipa_user(self, username: str) -> FreeIPAUser:
        return FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": [f"{username}@example.com"],
            },
        )

    def test_login_redirects_logged_in_user_to_profile(self) -> None:
        client = Client()
        self._login_as_freeipa(client, "alice")
        freeipa_user = self._make_freeipa_user("alice")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.views_users._get_full_user", return_value=freeipa_user),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = client.get("/login/", follow=True)

        profile_url = reverse("user-profile", kwargs={"username": "alice"})
        self.assertTrue(resp.redirect_chain)
        self.assertEqual(resp.redirect_chain[-1], (profile_url, 302))

    def test_password_expired_redirects_logged_in_user(self) -> None:
        client = Client()
        self._login_as_freeipa(client, "alice")
        freeipa_user = self._make_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=freeipa_user):
            resp = client.get("/password-expired/", follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("home"))


    def test_otp_sync_redirects_logged_in_user(self) -> None:
        client = Client()
        self._login_as_freeipa(client, "alice")
        freeipa_user = self._make_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=freeipa_user):
            resp = client.get("/otp/sync/", follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("home"))