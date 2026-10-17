import re
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class SidebarActiveLinkTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        session = self.client.session
        session["_freeipa_username"] = "alice"
        session.save()

        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []})
        patcher = patch("core.freeipa.user.FreeIPAUser.get", return_value=alice)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _my_profile_link_is_active(self, path: str) -> bool:
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200)
        own_profile_url = reverse("user-profile", args=["alice"])
        match = re.search(
            rf'<a href="{re.escape(own_profile_url)}" class="nav-link([^"]*)">\s*<i class="nav-icon fas fa-user"></i>',
            resp.content.decode(),
        )
        assert match is not None
        return "active" in match.group(1).split()

    def test_my_profile_is_active_on_own_profile(self) -> None:
        self.assertTrue(self._my_profile_link_is_active(reverse("user-profile", args=["alice"])))

    def test_my_profile_is_not_active_on_another_users_profile(self) -> None:
        self.assertFalse(self._my_profile_link_is_active(reverse("user-profile", args=["bob"])))
