
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from python_freeipa.exceptions import BadRequest

from core.freeipa.user import FreeIPAUser


class AdminIPAUserNoOpSaveTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_user_change_noop_does_not_500(self) -> None:
        """Submitting an unchanged admin user form should not 500.

        FreeIPA can respond to `user_mod` with "no modifications to be performed"
        when the request includes values that are already set.
        """

        username = "alex"
        freeipa_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["Admin"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        self._login_as_freeipa_admin(username)

        def fake_retry(get_client, fn):
            class DummyClient:
                def user_mod(self, _username: str, **_updates: object):
                    raise BadRequest("no modifications to be performed", 400)

            return fn(DummyClient())

        with (
            patch("core.admin.FreeIPAGroup.all", return_value=[SimpleNamespace(cn="admins")]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=fake_retry),
        ):
            url = reverse("admin:auth_ipauser_change", args=[username])
            resp = self.client.post(
                url,
                data={
                    "username": username,
                    "first_name": "Alex",
                    "last_name": "Admin",
                    "email": "alex@example.org",
                    "is_active": "on",
                    "groups": ["admins"],
                    "_save": "Save",
                },
                follow=False,
            )

        # Desired behavior: do not crash the admin on a no-op update.
        self.assertEqual(resp.status_code, 302)
