from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAGroup, FreeIPAUser


class GroupDetailModalConfirmTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_uses_modals_instead_of_confirm(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "parent",
            {
                "cn": ["parent"],
                "description": ["Some group"],
                "member_user": ["admin", "alice"],
                "member_group": [],
                "membermanager_user": ["admin"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "displayname": ["Administrator"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.backends.FreeIPAGroup.get", return_value=group),
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
        ):
            resp = self.client.get("/group/parent/")

        self.assertEqual(resp.status_code, 200)

        # The old inline `confirm()` prompts should be gone.
        self.assertNotContains(resp, "return confirm(")
        self.assertNotContains(resp, "onsubmit=\"return confirm")

        # The page should render Bootstrap confirm modals for these actions.
        self.assertContains(resp, 'id="leave-group-modal"')
        self.assertContains(resp, 'id="stop-sponsoring-modal"')
        self.assertContains(resp, 'id="remove-member-modal"')
