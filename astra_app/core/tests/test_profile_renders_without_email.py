
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


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

        with (
            patch("core.views_users._get_full_user", return_value=fu),
            patch("core.views_users._is_membership_committee_viewer", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            resp = self.client.get(reverse("api-user-profile", args=[username]))

        # Desired behavior: profile API should render even without an email.
        self.assertEqual(resp.status_code, 200)
