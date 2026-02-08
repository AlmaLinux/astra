
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class SidebarAdminLinkTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_sidebar_hides_admin_link_for_non_staff(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer User"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, f'href="{reverse("admin:index")}"')

    def test_sidebar_shows_admin_link_for_staff(self) -> None:
        self._login_as_freeipa_user("admin")

        admin = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "displayname": ["Admin User"],
                "memberof_group": ["admins"],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=admin):
            resp = self.client.get(reverse("elections"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'href="{reverse("admin:index")}"')
