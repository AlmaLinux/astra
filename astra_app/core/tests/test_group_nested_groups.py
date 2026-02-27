
from unittest.mock import patch

from django.test import TestCase

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class GroupNestedGroupsDisplayTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_shows_nested_groups_first_sorted_then_users(self) -> None:
        self._login_as_freeipa("admin")

        parent = FreeIPAGroup(
            "parent",
            {
                "cn": ["parent"],
                "description": [""],
                "member_user": ["bob"],
                "member_group": ["child", "alpha"],
                "objectclass": ["fasgroup"],
            },
        )
        alpha = FreeIPAGroup(
            "alpha",
            {
                "cn": ["alpha"],
                "description": [""],
                "member_user": ["zara"],
                "member_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        grand = FreeIPAGroup(
            "grand",
            {
                "cn": ["grand"],
                "description": [""],
                "member_user": ["carol"],
                "member_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        child = FreeIPAGroup(
            "child",
            {
                "cn": ["child"],
                "description": [""],
                "member_user": ["alice", "bob"],
                "member_group": ["grand"],
                "objectclass": ["fasgroup"],
            },
        )

        def _fake_group_get(cn: str):
            return {
                "parent": parent,
                "alpha": alpha,
                "child": child,
                "grand": grand,
            }.get(cn)

        def _fake_user_get(username: str) -> FreeIPAUser:
            return FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "givenname": [username.title()],
                    "sn": ["User"],
                    "mail": [f"{username}@example.com"],
                    "memberof_group": [],
                },
            )

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", side_effect=_fake_group_get),
            patch("core.templatetags.core_user_widget.FreeIPAUser.get", side_effect=_fake_user_get),
        ):
            resp = self.client.get("/group/parent/")

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Nested groups should be rendered using the group widget.
        self.assertIn("widget-group", html)

        idx_alpha = html.find('href="/group/alpha/"')
        idx_child = html.find('href="/group/child/"')
        idx_bob = html.find('href="/user/bob/"')

        self.assertGreaterEqual(idx_alpha, 0)
        self.assertGreaterEqual(idx_child, 0)
        self.assertGreaterEqual(idx_bob, 0)

        # Groups first, then users. Groups sorted alpha -> child.
        self.assertLess(idx_alpha, idx_child)
        self.assertLess(idx_child, idx_bob)

        # Child includes nested group 'grand' (carol), so the nested-aware count is 3 unique users.
        self.assertIn("child", html)
        self.assertIn(">3<", html)

    def test_group_detail_skips_non_fasgroup_member_groups_and_members(self) -> None:
        self._login_as_freeipa("admin")

        parent = FreeIPAGroup(
            "parent",
            {
                "cn": ["parent"],
                "description": [""],
                "member_user": ["bob"],
                "member_group": ["child", "legacy"],
                "objectclass": ["fasgroup"],
            },
        )
        child = FreeIPAGroup(
            "child",
            {
                "cn": ["child"],
                "description": [""],
                "member_user": ["alice"],
                "member_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        legacy = FreeIPAGroup(
            "legacy",
            {
                "cn": ["legacy"],
                "description": [""],
                "member_user": ["zara"],
                "member_group": [],
                "objectclass": [],
            },
        )

        def _fake_group_get(cn: str):
            return {
                "parent": parent,
                "child": child,
                "legacy": legacy,
            }.get(cn)

        def _fake_user_get(username: str) -> FreeIPAUser:
            return FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "givenname": [username.title()],
                    "sn": ["User"],
                    "mail": [f"{username}@example.com"],
                    "memberof_group": [],
                },
            )

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", side_effect=_fake_group_get),
            patch("core.templatetags.core_user_widget.FreeIPAUser.get", side_effect=_fake_user_get),
        ):
            resp = self.client.get("/group/parent/")

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertNotIn('href="/group/legacy/"', html)
        self.assertIn('href="/group/child/"', html)
        self.assertIn("(2 members)", html)
