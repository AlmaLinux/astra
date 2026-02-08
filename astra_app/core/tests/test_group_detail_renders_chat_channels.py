
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAGroup


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
                    "irc:/#dev",
                    "matrix://matrix.org/#almalinux",
                    "mattermost://chat.almalinux.org/almalinux/channels/general",
                ],
                "objectclass": ["fasgroup"],
            },
        )

        with patch("core.backends.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/fas1/")

        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, 'href="ircs://irc.libera.chat/#dev"')
        self.assertContains(resp, ">#dev</a>")

        self.assertContains(resp, 'href="https://matrix.to/#/#almalinux:matrix.org')
        self.assertContains(resp, ">#almalinux</a>")

        self.assertContains(resp, 'href="mattermost://chat.almalinux.org/almalinux/channels/general"')
        self.assertContains(resp, ">~general</a>")
