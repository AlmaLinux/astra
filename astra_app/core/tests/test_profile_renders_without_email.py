
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAUser


class ProfileRenderingWithoutEmailTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_profile_page_renders_when_freeipa_user_has_no_email(self):
        """Regression: django-avatar gravatar provider crashes on email=None."""

        username = "admin"
        self._login_as_freeipa(username)

        # Simulate a FreeIPA user record missing the 'mail' attribute.
        fu = FreeIPAUser(username, {"uid": [username], "givenname": ["A"], "sn": ["Dmin"]})
        # Missing mail should not crash avatar providers.
        self.assertEqual(fu.email, "")

        with patch("core.backends.FreeIPAUser.get", return_value=fu):
            resp = self.client.get(f"/user/{username}/")

        # Desired behavior: profile page should render even without an email.
        self.assertEqual(resp.status_code, 200)
