
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from python_freeipa.exceptions import Denied

from core.backends import FreeIPAGroup, FreeIPAUser


class AdminDeleteSelectedIPAGroupTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_bulk_delete_denied_shows_error_message(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        target_group = FreeIPAGroup("testgroup", {"cn": ["testgroup"], "description": ["d"], "member": []})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.backends.FreeIPAGroup.all", return_value=[target_group]),
            patch("core.backends.FreeIPAGroup.get", return_value=target_group),
            patch.object(target_group, "delete", side_effect=Denied("Insufficient access", 0)),
        ):
            url = reverse("admin:auth_ipagroup_changelist")
            confirm = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["testgroup"],
                },
                follow=False,
            )

            self.assertEqual(confirm.status_code, 200)
            self.assertContains(confirm, "Are you sure", status_code=200)

            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["testgroup"],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Failed to delete")
        self.assertContains(resp, "Insufficient access")

    def test_delete_view_denied_shows_error_message(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        target_group = FreeIPAGroup("testgroup", {"cn": ["testgroup"], "description": ["d"], "member": []})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.backends.FreeIPAGroup.get", return_value=target_group),
            patch.object(target_group, "delete", side_effect=Denied("Insufficient access", 0)),
        ):
            url = reverse("admin:auth_ipagroup_delete", args=["testgroup"])
            resp = self.client.post(url, data={"post": "yes"}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Insufficient access")
