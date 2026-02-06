from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.backends import FreeIPAUser


class GroupDetailPromoteMemberToSponsorTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_shows_promote_button_for_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = SimpleNamespace(
            cn="testgroup",
            fas_group=True,
            description="",
            members=["member1"],
            sponsors=["sponsor1"],
            sponsor_groups=[],
            member_groups=[],
            fas_url=None,
            fas_mailing_list=None,
            fas_irc_channels=[],
            fas_discussion_url=None,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=sponsor),
            patch("core.backends.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/testgroup/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-target="#promote-member-modal"')
        self.assertContains(resp, 'data-username="member1"')
        self.assertContains(resp, "fa-person-arrow-up-from-line")

    def test_group_detail_shows_demote_button_for_other_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = SimpleNamespace(
            cn="testgroup",
            fas_group=True,
            description="",
            members=["member1"],
            sponsors=["sponsor1", "sponsor2"],
            sponsor_groups=[],
            member_groups=[],
            fas_url=None,
            fas_mailing_list=None,
            fas_irc_channels=[],
            fas_discussion_url=None,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=sponsor),
            patch("core.backends.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/testgroup/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-target="#demote-sponsor-modal"')
        self.assertContains(resp, 'data-username="sponsor2"')
        self.assertContains(resp, "fa-person-arrow-down-from-line")

    def test_sponsor_can_promote_member_to_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = SimpleNamespace(
            cn="testgroup",
            fas_group=True,
            description="",
            members=["member1"],
            sponsors=["sponsor1"],
            sponsor_groups=[],
            member_groups=[],
            fas_url=None,
            fas_mailing_list=None,
            fas_irc_channels=[],
            fas_discussion_url=None,
            add_sponsor=MagicMock(),
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=sponsor),
            patch("core.backends.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                "/group/testgroup/",
                data={"action": "promote_member", "username": "member1"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        group.add_sponsor.assert_called_once_with("member1")

    def test_sponsor_can_demote_other_sponsor(self) -> None:
        self._login_as_freeipa("sponsor1")

        sponsor = FreeIPAUser("sponsor1", {"uid": ["sponsor1"], "memberof_group": []})
        group = SimpleNamespace(
            cn="testgroup",
            fas_group=True,
            description="",
            members=["member1"],
            sponsors=["sponsor1", "sponsor2"],
            sponsor_groups=[],
            member_groups=[],
            fas_url=None,
            fas_mailing_list=None,
            fas_irc_channels=[],
            fas_discussion_url=None,
            remove_sponsor=MagicMock(),
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=sponsor),
            patch("core.backends.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                "/group/testgroup/",
                data={"action": "demote_sponsor", "username": "sponsor2"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        group.remove_sponsor.assert_called_once_with("sponsor2")
