
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup


class GroupDetailRendersChatChannelsTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_renders_chat_channels_links(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "description": ["FAS Group 1"],
                "member_user": [],
                "member_group": [],
                "membermanager_user": [],
                "membermanager_group": [],
                "fasircchannel": [
                    "irc://#dev",
                    "matrix://matrix.org/#almalinux",
                    "mattermost://chat.almalinux.org/almalinux/channels/general",
                    "mattermost://channels/atomicsig",
                ],
                "objectclass": ["fasgroup"],
            },
        )

        with patch("core.freeipa.group.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/fas1/")
            info_resp = self.client.get(reverse("api-group-detail-info", args=["fas1"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-group-detail-root")
        self.assertContains(resp, reverse("api-group-detail-info", args=["fas1"]))
        self.assertEqual(info_resp.status_code, 200)
        self.assertEqual(
            info_resp.json()["group"]["fas_irc_channels"],
            [
                "irc://#dev",
                "matrix://matrix.org/#almalinux",
                "mattermost://chat.almalinux.org/almalinux/channels/general",
                "mattermost://channels/atomicsig",
            ],
        )
