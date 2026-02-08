
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAGroup, FreeIPAUser


class AdminPermissionGrantPrincipalsEndpointTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_principals_endpoint_returns_users_for_user_type(self) -> None:
        admin_username = "alice"
        freeipa_admin = FreeIPAUser(admin_username, {"uid": [admin_username], "memberof_group": ["admins"]})

        self._login_as_freeipa_admin(admin_username)

        url = reverse("admin:core_freeipapermissiongrant_principals")

        with patch("core.backends.FreeIPAUser.get", return_value=freeipa_admin):
            with patch(
                "core.admin.FreeIPAUser.all",
                return_value=[
                    FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": []}),
                    FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice Example"], "memberof_group": []}),
                ],
            ):
                resp = self.client.get(url, data={"principal_type": "user"}, follow=False)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["principal_type"], "user")
        self.assertEqual(
            data["principals"],
            [
                {"id": "alice", "text": "Alice Example (alice)"},
                {"id": "bob", "text": "Bob Example (bob)"},
            ],
        )

    def test_principals_endpoint_returns_groups_for_group_type(self) -> None:
        admin_username = "alice"
        freeipa_admin = FreeIPAUser(admin_username, {"uid": [admin_username], "memberof_group": ["admins"]})

        self._login_as_freeipa_admin(admin_username)

        url = reverse("admin:core_freeipapermissiongrant_principals")

        with patch("core.backends.FreeIPAUser.get", return_value=freeipa_admin):
            with patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[
                    FreeIPAGroup("group-b"),
                    FreeIPAGroup("group-a"),
                ],
            ):
                resp = self.client.get(url, data={"principal_type": "group"}, follow=False)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # The view echoes back the param when valid.
        self.assertEqual(data["principal_type"], "group")
        self.assertEqual(
            data["principals"],
            [
                {"id": "group-a", "text": "group-a"},
                {"id": "group-b", "text": "group-b"},
            ],
        )
