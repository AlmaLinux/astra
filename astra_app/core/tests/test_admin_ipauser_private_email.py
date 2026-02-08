
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class AdminIPAUserPrivateEmailTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "admin") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_change_form_shows_email_for_private_user(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return FreeIPAUser(
                    "sej7278",
                    {
                        "uid": ["sej7278"],
                        "mail": ["sej7278@example.org"],
                        "fasIsPrivate": ["TRUE"],
                        "memberof_group": [],
                    },
                )
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="sej7278@example.org"')
