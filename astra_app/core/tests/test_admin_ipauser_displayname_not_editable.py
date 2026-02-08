
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class AdminIPAUserDisplayNameNotEditableTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_change_form_does_not_render_displayname_field(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        target_user = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _fake_user_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["bob"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertNotIn("Display name", html)
