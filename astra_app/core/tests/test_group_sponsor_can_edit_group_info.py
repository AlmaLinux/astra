import json
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class GroupSponsorCanEditGroupInfoTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _group(self, *, sponsors: list[str]) -> FreeIPAGroup:
        return FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": ["FAS Group 1"],
                "member_user": ["bob", "alice"],
                "member_group": [],
                "membermanager_user": sponsors,
                "membermanager_group": [],
                "fasurl": ["https://example.org/group/fas1"],
                "fasmailinglist": ["fas1@example.org"],
                "fasircchannel": ["#fas1"],
                "fasdiscussionurl": ["https://discussion.example.org/c/fas1"],
                "objectclass": ["fasgroup"],
            },
        )

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
    def test_sponsor_can_get_edit_route_shell(self) -> None:
        self._login_as_freeipa("bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        group = self._group(sponsors=["bob"])

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/fas1/edit/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-group-form-root")
        self.assertContains(resp, 'data-group-form-api-url="/api/v1/groups/fas1/edit"')
        self.assertContains(resp, 'data-group-form-detail-url="/group/fas1/"')
        self.assertContains(resp, 'src="http://localhost:5173/src/entrypoints/groupForm.ts"')

    def test_sponsor_can_get_edit_api_prefilled(self) -> None:
        self._login_as_freeipa("bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        group = self._group(sponsors=["bob"])

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get(reverse("api-group-edit", args=["fas1"]), HTTP_ACCEPT="application/json")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertEqual(payload["group"]["cn"], "fas1")
        self.assertEqual(payload["group"]["description"], "FAS Group 1")
        self.assertEqual(payload["group"]["fas_irc_channels"], ["#fas1"])

    def test_sponsor_can_put_updates_via_api(self) -> None:
        self._login_as_freeipa("bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        group = self._group(sponsors=["bob"])
        group.save = MagicMock()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.put(
                reverse("api-group-edit", args=["fas1"]),
                data=json.dumps(
                    {
                        "description": "Updated desc",
                        "fas_url": "https://example.org/new",
                        "fas_mailing_list": "new@example.org",
                        "fas_irc_channels": "#new\n#new-dev",
                        "fas_discussion_url": "https://discussion.example.org/c/new",
                    }
                ),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(group.description, "Updated desc")
        self.assertEqual(group.fas_url, "https://example.org/new")
        self.assertEqual(group.fas_mailing_list, "new@example.org")
        self.assertEqual(sorted(group.fas_irc_channels), ["irc://#new", "irc://#new-dev"])
        self.assertEqual(group.fas_discussion_url, "https://discussion.example.org/c/new")
        group.save.assert_called_once()

    def test_sponsor_invalid_put_returns_validation_errors(self) -> None:
        self._login_as_freeipa("bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        group = self._group(sponsors=["bob"])
        group.save = MagicMock()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.put(
                reverse("api-group-edit", args=["fas1"]),
                data=json.dumps(
                    {
                        "description": "Updated desc",
                        "fas_url": "not-a-url",
                        "fas_mailing_list": "new@example.org",
                        "fas_irc_channels": "#new\n#new-dev",
                        "fas_discussion_url": "https://discussion.example.org/c/new",
                    }
                ),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        payload = json.loads(resp.content)
        self.assertFalse(payload["ok"])
        self.assertIn("fas_url", payload["errors"])
        group.save.assert_not_called()

    def test_sponsor_save_connection_error_returns_unavailable_json(self) -> None:
        self._login_as_freeipa("bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        group = self._group(sponsors=["bob"])
        group.save = MagicMock(side_effect=requests.exceptions.ConnectionError())

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.put(
                reverse("api-group-edit", args=["fas1"]),
                data=json.dumps(
                    {
                        "description": "Updated desc",
                        "fas_url": "https://example.org/new",
                        "fas_mailing_list": "new@example.org",
                        "fas_irc_channels": "#new",
                        "fas_discussion_url": "https://discussion.example.org/c/new",
                    }
                ),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 503)
        payload = json.loads(resp.content)
        self.assertFalse(payload["ok"])
        self.assertIn("temporarily unavailable", payload["error"])

    def test_non_sponsor_forbidden_on_route_and_api(self) -> None:
        self._login_as_freeipa("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        group = self._group(sponsors=["bob"])

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            route_resp = self.client.get("/group/fas1/edit/")
            api_resp = self.client.get(reverse("api-group-edit", args=["fas1"]), HTTP_ACCEPT="application/json")

        self.assertEqual(route_resp.status_code, 403)
        self.assertEqual(api_resp.status_code, 403)
        payload = json.loads(api_resp.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Only sponsors can edit group info.")
