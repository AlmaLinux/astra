
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.freeipa.group import FreeIPAGroup


class GroupRoutesTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_groups_route_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa("admin")

        resp = self.client.get("/groups/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-groups-root")
        self.assertContains(resp, 'data-groups-api-url="/api/v1/groups"')
        self.assertContains(resp, 'src="http://localhost:5173/src/entrypoints/groups.ts"')
        self.assertContains(resp, "Loading groups...")

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_groups_route_shell_does_not_prerender_groups_table(self) -> None:
        self._login_as_freeipa("admin")

        resp = self.client.get("/groups/")

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<table")
        self.assertNotContains(resp, "No groups found.")


class GroupDetailRouteTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_group_detail_route_renders_vue_shell_contract(self) -> None:
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

        with patch("core.freeipa.group.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/fas1/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-group-detail-root")
        self.assertContains(resp, 'data-group-detail-info-api-url="/api/v1/groups/fas1/info"')
        self.assertContains(resp, 'data-group-detail-leaders-api-url="/api/v1/groups/fas1/leaders"')
        self.assertContains(resp, 'data-group-detail-members-api-url="/api/v1/groups/fas1/members"')
        self.assertContains(resp, 'data-group-detail-action-url="/api/v1/groups/fas1/action"')
        self.assertContains(resp, 'src="http://localhost:5173/src/entrypoints/groupDetail.ts"')
        self.assertContains(resp, "Loading group details...")

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

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_group_detail_route_shell_does_not_prerender_members_grid(self) -> None:
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

        with patch("core.freeipa.group.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/fas1/")

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Search members...")
        self.assertNotContains(resp, "Search group members")


class GroupEditRouteTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_group_edit_route_renders_vue_shell_contract_for_sponsor(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": ["FAS Group 1"],
                "member_user": ["admin"],
                "member_group": [],
                "membermanager_user": ["admin"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        with patch("core.freeipa.group.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/fas1/edit/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-group-form-root")
        self.assertContains(resp, 'data-group-form-api-url="/api/v1/groups/fas1/edit"')
        self.assertContains(resp, 'data-group-form-detail-url="/group/fas1/"')
        self.assertContains(resp, 'src="http://localhost:5173/src/entrypoints/groupForm.ts"')
        self.assertContains(resp, "Loading group editor...")

    def test_group_edit_route_forbidden_for_non_sponsor(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": ["FAS Group 1"],
                "member_user": ["admin"],
                "member_group": [],
                "membermanager_user": ["bob"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        with patch("core.freeipa.group.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/fas1/edit/")

        self.assertEqual(resp.status_code, 403)
