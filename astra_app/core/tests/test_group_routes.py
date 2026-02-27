
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class GroupRoutesTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_groups_route_filters_to_fasgroups(self) -> None:
        self._login_as_freeipa("admin")

        groups = [
            SimpleNamespace(cn="fas1", description="FAS Group 1", fas_group=True, member_count_recursive=lambda: 0),
            SimpleNamespace(cn="ipa_only", description="Not a FAS group", fas_group=False, member_count_recursive=lambda: 0),
            SimpleNamespace(cn="fas2", description="FAS Group 2", fas_group=True, member_count_recursive=lambda: 0),
        ]

        with patch("core.freeipa.group.FreeIPAGroup.all", return_value=groups):
            resp = self.client.get("/groups/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "fas1")
        self.assertContains(resp, "fas2")
        self.assertNotContains(resp, "ipa_only")

    def test_groups_route_paginates_30_per_page(self) -> None:
        self._login_as_freeipa("admin")

        groups = [
            SimpleNamespace(cn=f"group{i:03d}", description="", fas_group=True, member_count_recursive=lambda: 0)
            for i in range(65)
        ]

        with patch("core.freeipa.group.FreeIPAGroup.all", return_value=groups):
            resp_page_1 = self.client.get("/groups/")
            resp_page_2 = self.client.get("/groups/?page=2")

        self.assertEqual(resp_page_1.status_code, 200)
        self.assertContains(resp_page_1, "group000")
        self.assertContains(resp_page_1, "group029")
        self.assertNotContains(resp_page_1, "group030")

        self.assertEqual(resp_page_2.status_code, 200)
        self.assertContains(resp_page_2, "group030")

    def test_groups_route_search_filters_results(self) -> None:
        self._login_as_freeipa("admin")

        groups = [
            SimpleNamespace(cn="infra", description="Infrastructure", fas_group=True, member_count_recursive=lambda: 0),
            SimpleNamespace(cn="docs", description="Documentation", fas_group=True, member_count_recursive=lambda: 0),
        ]

        with patch("core.freeipa.group.FreeIPAGroup.all", return_value=groups):
            resp = self.client.get("/groups/?q=inf")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "infra")
        self.assertNotContains(resp, "docs")

    def test_groups_route_shows_member_count(self) -> None:
        self._login_as_freeipa("admin")

        groups = [
            SimpleNamespace(cn="fas1", description="", fas_group=True, member_count_recursive=lambda: 2),
            SimpleNamespace(cn="fas2", description="", fas_group=True, member_count_recursive=lambda: 0),
        ]

        with patch("core.freeipa.group.FreeIPAGroup.all", return_value=groups):
            resp = self.client.get("/groups/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "fas1")
        self.assertContains(resp, ">2<")
        self.assertContains(resp, "fas2")
        self.assertContains(resp, ">0<")


class GroupDetailRouteTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_route_renders_info_and_members(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": ["FAS Group 1"],
                "member_user": ["alice", "bob"],
                "member_group": [],
                "membermanager_user": [],
                "membermanager_group": [],
                "fasurl": ["https://example.org/group/fas1"],
                "fasmailinglist": ["fas1@example.org"],
                "fasircchannel": ["#fas1"],
                "fasdiscussionurl": ["https://discussion.example.org/c/fas1"],
                "objectclass": ["fasgroup"],
            },
        )

        def _fake_user_get(username: str) -> FreeIPAUser:
            return FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "givenname": [username.capitalize()],
                    "sn": ["User"],
                    "mail": [""],
                },
            )

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
            patch("core.templatetags.core_user_widget.FreeIPAUser.get", side_effect=_fake_user_get),
        ):
            resp = self.client.get("/group/fas1/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "fas1")
        self.assertContains(resp, "FAS Group 1")
        self.assertContains(resp, "https://example.org/group/fas1")
        self.assertContains(resp, "fas1@example.org")
        self.assertContains(resp, "#fas1")
        self.assertContains(resp, "https://discussion.example.org/c/fas1")
        self.assertContains(resp, 'href="/user/alice/"')
        self.assertContains(resp, 'href="/user/bob/"')

    def test_group_detail_route_404_for_non_fas_group(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "ipa_only",
            {
                "cn": ["ipa_only"],
                "description": [""],
                "member_user": ["alice"],
                "member_group": [],
                "objectclass": [],
            },
        )

        with patch("core.freeipa.group.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/ipa_only/")

        self.assertEqual(resp.status_code, 404)

    def test_group_detail_members_paginate_30_per_page(self) -> None:
        self._login_as_freeipa("admin")

        members = [f"user{i:03d}" for i in range(65)]
        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": [""],
                "member_user": members,
                "member_group": [],
                "membermanager_user": [],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        def _fake_user_get(username: str) -> FreeIPAUser:
            return FreeIPAUser(username, {"uid": [username], "givenname": [""], "sn": [""], "mail": [""]})

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
            patch("core.templatetags.core_user_widget.FreeIPAUser.get", side_effect=_fake_user_get),
        ):
            resp_page_1 = self.client.get("/group/fas1/")
            resp_page_2 = self.client.get("/group/fas1/?page=2")

        self.assertEqual(resp_page_1.status_code, 200)
        self.assertContains(resp_page_1, 'href="/user/user000/"')
        self.assertContains(resp_page_1, 'href="/user/user027/"')
        self.assertNotContains(resp_page_1, 'href="/user/user028/"')

        self.assertEqual(resp_page_2.status_code, 200)
        self.assertContains(resp_page_2, 'href="/user/user028/"')

    def test_group_detail_members_search_filters(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": [""],
                "member_user": ["alice", "bob"],
                "member_group": [],
                "membermanager_user": [],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        def _fake_user_get(username: str) -> FreeIPAUser:
            return FreeIPAUser(username, {"uid": [username], "givenname": [""], "sn": [""], "mail": [""]})

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
            patch("core.templatetags.core_user_widget.FreeIPAUser.get", side_effect=_fake_user_get),
        ):
            resp = self.client.get("/group/fas1/?q=ali")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/user/alice/"')
        self.assertNotContains(resp, 'href="/user/bob/"')
