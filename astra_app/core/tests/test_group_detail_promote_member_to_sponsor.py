
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class GroupDetailPromoteMemberToSponsorTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_shows_promote_button_for_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": [""],
                "member_user": ["member1"],
                "member_group": [],
                "membermanager_user": ["sponsor1"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=sponsor),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/testgroup/")
            members_resp = self.client.get(reverse("api-group-detail-members", args=["testgroup"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-group-detail-root")
        self.assertContains(resp, reverse("api-group-detail-members", args=["testgroup"]))
        self.assertEqual(members_resp.status_code, 200)
        member_items = members_resp.json()["members"]["items"]
        self.assertTrue(any(item.get("username") == "member1" and not item.get("is_leader") for item in member_items))

    def test_group_detail_shows_demote_button_for_other_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": [""],
                "member_user": ["member1"],
                "member_group": [],
                "membermanager_user": ["sponsor1", "sponsor2"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=sponsor),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/testgroup/")
            leaders_resp = self.client.get(reverse("api-group-detail-leaders", args=["testgroup"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-group-detail-root")
        self.assertContains(resp, reverse("api-group-detail-leaders", args=["testgroup"]))
        self.assertEqual(leaders_resp.status_code, 200)
        leader_items = leaders_resp.json()["leaders"]["items"]
        self.assertTrue(any(item.get("kind") == "user" and item.get("username") == "sponsor2" for item in leader_items))

    def test_sponsor_can_promote_member_to_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": [""],
                "member_user": ["member1"],
                "member_group": [],
                "membermanager_user": ["sponsor1"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        group.add_sponsor = MagicMock()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=sponsor),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                reverse("api-group-action", args=["testgroup"]),
                data=json.dumps({"action": "promote_member", "username": "member1"}),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        group.add_sponsor.assert_called_once_with("member1")

    def test_sponsor_can_demote_other_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": [""],
                "member_user": ["member1"],
                "member_group": [],
                "membermanager_user": ["sponsor1", "sponsor2"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        group.remove_sponsor = MagicMock()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=sponsor),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                reverse("api-group-action", args=["testgroup"]),
                data=json.dumps({"action": "demote_sponsor", "username": "sponsor2"}),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        group.remove_sponsor.assert_called_once_with("sponsor2")
