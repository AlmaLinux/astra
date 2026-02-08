
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import IPAUser


class AdminIPAUserPasswordFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        ContentType.objects.get_for_model(IPAUser)

    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_user_change_form_hides_password_field(self) -> None:
        username = "alex"
        self._login_as_freeipa_admin(username)
        target_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["User"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        def fake_user_get(u: str):
            # Middleware loads session user; admin change view loads edited user.
            if u == username:
                return target_user
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=fake_user_get),
            patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[SimpleNamespace(cn="admins"), SimpleNamespace(cn="ambassadors")],
            ),
        ):
            resp = self.client.get(reverse("admin:auth_ipauser_change", args=[username]))

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertNotIn('name="password"', content)

    def test_admin_user_add_form_shows_password_field(self) -> None:
        self._login_as_freeipa_admin("alex")

        admin_user = FreeIPAUser("alex", {"uid": ["alex"], "memberof_group": ["admins"]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[SimpleNamespace(cn="admins"), SimpleNamespace(cn="ambassadors")],
            ),
        ):
            resp = self.client.get(reverse("admin:auth_ipauser_add"))

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn('name="password"', content)
