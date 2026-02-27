
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.contrib.messages import get_messages
from django.test import TestCase

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class GroupSponsorCanEditGroupInfoTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_shows_edit_button_for_sponsor(self) -> None:
        self._login_as_freeipa("bob")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": ["FAS Group 1"],
                "member_user": [],
                "member_group": [],
                "membermanager_user": ["bob"],
                "membermanager_group": [],
                "fasurl": ["https://example.org/group/fas1"],
                "fasmailinglist": ["fas1@example.org"],
                "fasircchannel": ["#fas1"],
                "fasdiscussionurl": ["https://discussion.example.org/c/fas1"],
                "objectclass": ["fasgroup"],
            },
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/fas1/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/group/fas1/edit/"')
        self.assertContains(resp, "Edit group")

    def test_sponsor_can_get_edit_form_prefilled(self) -> None:
        self._login_as_freeipa("bob")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        group = SimpleNamespace(
            cn="fas1",
            description="FAS Group 1",
            fas_group=True,
            fas_url="https://example.org/group/fas1",
            fas_mailing_list="fas1@example.org",
            fas_irc_channels=["#fas1"],
            fas_discussion_url="https://discussion.example.org/c/fas1",
            members=[],
            sponsors=["bob"],
            sponsor_groups=[],
            save=MagicMock(),
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/fas1/edit/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="description"')
        self.assertContains(resp, 'id="id_description"')
        self.assertContains(resp, 'FAS Group 1</textarea>')
        self.assertContains(resp, 'name="fas_url"')
        self.assertContains(resp, 'value="https://example.org/group/fas1"')
        self.assertContains(resp, 'name="fas_mailing_list"')
        self.assertContains(resp, 'value="fas1@example.org"')
        self.assertContains(resp, "#fas1")
        self.assertContains(resp, 'name="fas_discussion_url"')
        self.assertContains(resp, 'value="https://discussion.example.org/c/fas1"')

        # Group chat values should use the reusable chat channels editor.
        self.assertContains(resp, "core/js/chat_channels_editor.js")
        self.assertContains(resp, 'class="d-none js-chat-channels-editor"')
        self.assertContains(resp, 'data-textarea-id="id_fas_irc_channels"')
        self.assertContains(resp, 'data-mattermost-default-server="chat.almalinux.org"')
        self.assertContains(resp, 'data-mattermost-default-team="almalinux"')

    def test_sponsor_can_post_updates(self) -> None:
        self._login_as_freeipa("bob")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        group = SimpleNamespace(
            cn="fas1",
            description="FAS Group 1",
            fas_group=True,
            fas_url="https://example.org/group/fas1",
            fas_mailing_list="fas1@example.org",
            fas_irc_channels=["#fas1"],
            fas_discussion_url="https://discussion.example.org/c/fas1",
            members=[],
            sponsors=["bob"],
            sponsor_groups=[],
            save=MagicMock(),
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                "/group/fas1/edit/",
                {
                    "description": "Updated desc",
                    "fas_url": "https://example.org/new",
                    "fas_mailing_list": "new@example.org",
                    "fas_irc_channels": "#new\n#new-dev",
                    "fas_discussion_url": "https://discussion.example.org/c/new",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/group/fas1/")

        self.assertEqual(group.description, "Updated desc")
        self.assertEqual(group.fas_url, "https://example.org/new")
        self.assertEqual(group.fas_mailing_list, "new@example.org")
        self.assertEqual(sorted(group.fas_irc_channels), ["irc:/#new", "irc:/#new-dev"])
        self.assertEqual(group.fas_discussion_url, "https://discussion.example.org/c/new")
        group.save.assert_called_once()

    def test_sponsor_invalid_post_renders_bootstrap_validation_feedback(self) -> None:
        self._login_as_freeipa("bob")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        group = SimpleNamespace(
            cn="fas1",
            description="FAS Group 1",
            fas_group=True,
            fas_url="https://example.org/group/fas1",
            fas_mailing_list="fas1@example.org",
            fas_irc_channels=["#fas1"],
            fas_discussion_url="https://discussion.example.org/c/fas1",
            members=[],
            sponsors=["bob"],
            sponsor_groups=[],
            save=MagicMock(),
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                "/group/fas1/edit/",
                {
                    "description": "Updated desc",
                    "fas_url": "not-a-url",
                    "fas_mailing_list": "new@example.org",
                    "fas_irc_channels": "#new\n#new-dev",
                    "fas_discussion_url": "https://discussion.example.org/c/new",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid URL")
        self.assertContains(resp, "invalid-feedback")
        self.assertContains(resp, "was-validated")
        group.save.assert_not_called()

    def test_sponsor_can_post_mattermost_channel_with_tilde(self) -> None:
        self._login_as_freeipa("bob")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        group = SimpleNamespace(
            cn="fas1",
            description="FAS Group 1",
            fas_group=True,
            fas_url=None,
            fas_mailing_list=None,
            fas_irc_channels=[],
            fas_discussion_url=None,
            members=[],
            sponsors=["bob"],
            sponsor_groups=[],
            save=MagicMock(),
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                "/group/fas1/edit/",
                {
                    "description": "FAS Group 1",
                    "fas_irc_channels": "~atomicsig",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/group/fas1/")
        self.assertEqual(group.fas_irc_channels, ["mattermost:/channels/atomicsig"])
        group.save.assert_called_once()

    def test_sponsor_save_connection_error_shows_unavailable_message(self) -> None:
        self._login_as_freeipa("bob")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        group = SimpleNamespace(
            cn="fas1",
            description="FAS Group 1",
            fas_group=True,
            fas_url="https://example.org/group/fas1",
            fas_mailing_list="fas1@example.org",
            fas_irc_channels=["#fas1"],
            fas_discussion_url="https://discussion.example.org/c/fas1",
            members=[],
            sponsors=["bob"],
            sponsor_groups=[],
            save=MagicMock(side_effect=requests.exceptions.ConnectionError()),
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=bob),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.post(
                "/group/fas1/edit/",
                {
                    "description": "Updated desc",
                    "fas_url": "https://example.org/new",
                    "fas_mailing_list": "new@example.org",
                    "fas_irc_channels": "#new\n#new-dev",
                    "fas_discussion_url": "https://discussion.example.org/c/new",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        msgs = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn(
            "This action cannot be completed right now because AlmaLinux Accounts is temporarily unavailable. "
            "Please try again later.",
            msgs,
        )

    def test_non_sponsor_forbidden(self) -> None:
        self._login_as_freeipa("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        group = SimpleNamespace(
            cn="fas1",
            description="FAS Group 1",
            fas_group=True,
            members=[],
            sponsors=["bob"],
            sponsor_groups=[],
            fas_url=None,
            fas_mailing_list=None,
            fas_irc_channels=[],
            fas_discussion_url=None,
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            resp = self.client.get("/group/fas1/edit/")

        self.assertEqual(resp.status_code, 403)
