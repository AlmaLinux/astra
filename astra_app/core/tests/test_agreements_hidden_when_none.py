
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class AgreementsHiddenWhenNoneTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_profile_hides_agreement_count_when_no_agreements_exist(self):
        username = "admin"
        self._login_as_freeipa(username)

        fu = FreeIPAUser(username, {"uid": [username], "givenname": ["A"], "sn": ["Dmin"], "mail": [""]})

        with patch("core.backends.FreeIPAUser.get", return_value=fu):
            with patch("core.backends.FreeIPAGroup.all", return_value=[]):
                with patch("core.backends.FreeIPAFASAgreement.all", return_value=[]):
                    resp = self.client.get(reverse("user-profile", args=[username]))

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Agreement(s)", resp.content)

    def test_settings_tabs_hide_agreements_when_no_agreements_exist(self):
        username = "alice"
        self._login_as_freeipa(username)

        middleware_user = FreeIPAUser(username, {"uid": [username], "givenname": ["Alice"], "sn": ["User"]})

        fake_user = SimpleNamespace(
            username=username,
            first_name="Alice",
            last_name="User",
            email="a@example.org",
            is_authenticated=True,
            groups_list=[],
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "fasIsPrivate": ["FALSE"],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=middleware_user):
            with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
                with patch("core.backends.FreeIPAFASAgreement.all", return_value=[]):
                    resp = self.client.get(reverse("settings"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"#agreements", resp.content)
        self.assertNotIn(b">Agreements<", resp.content)
