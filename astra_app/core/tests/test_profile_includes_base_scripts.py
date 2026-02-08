
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAUser


class ProfileIncludesBaseScriptsTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_profile_page_includes_base_scripts_block(self) -> None:
        """Regression: user_profile.html must not shadow base.html scripts.

        Without including {{ block.super }}, the profile page becomes the only page
        missing AdminLTE/Bootstrap/jQuery scripts, which can cause layout glitches.
        """

        username = "admin"
        self._login_as_freeipa(username)

        fu = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["A"],
                "sn": ["Dmin"],
                "mail": ["admin@example.com"],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=fu):
            resp = self.client.get(f"/user/{username}/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'src="/static/admin/js/vendor/jquery/jquery.js"')
        self.assertContains(resp, 'src="/static/vendor/bootstrap/js/bootstrap.min.js"')
        self.assertContains(resp, 'src="/static/vendor/adminlte/js/adminlte.min.js"')
