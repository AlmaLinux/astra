
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser


class AdminDeleteSelectedIPAUserTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_bulk_delete_action_shows_confirmation(self) -> None:
        """Bulk-delete should render Django's confirmation page."""

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        target_user = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["Example"],
                "mail": ["bob@example.org"],
                "memberof_group": [],
            },
        )

        def _fake_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return target_user
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_get),
            patch("core.backends.FreeIPAUser.all", return_value=[target_user]),
            patch.object(target_user, "delete", return_value=None),
        ):
            url = reverse("admin:auth_ipauser_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["bob"],
                    "select_across": "1",
                    "index": "0",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Are you sure", status_code=200)
