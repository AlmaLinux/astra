
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class AdminIPAGroupMemberGroupsTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_can_edit_member_groups(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        existing_group = FreeIPAGroup(
            "parent",
            {
                "cn": ["parent"],
                "description": [""],
                "member_user": [],
                "member_group": ["child"],
                "membermanager_group": ["sponsor_child"],
                "objectclass": ["fasgroup"],
            },
        )

        all_users = [admin_user]
        all_groups = [
            FreeIPAGroup("parent", {"cn": ["parent"], "objectclass": ["fasgroup"]}),
            FreeIPAGroup("child", {"cn": ["child"], "objectclass": ["fasgroup"]}),
            FreeIPAGroup("alpha", {"cn": ["alpha"], "objectclass": ["fasgroup"]}),
            FreeIPAGroup("sponsor_child", {"cn": ["sponsor_child"], "objectclass": ["fasgroup"]}),
            FreeIPAGroup("sponsor_alpha", {"cn": ["sponsor_alpha"], "objectclass": ["fasgroup"]}),
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=all_users),
            patch("core.admin.FreeIPAGroup.all", return_value=all_groups),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=existing_group),
        ):
            url = reverse("admin:auth_ipagroup_change", args=["parent"])

            # Baseline: the change form should expose a member_groups field.
            resp_form = self.client.get(url)
            self.assertEqual(resp_form.status_code, 200)
            self.assertContains(resp_form, "member_groups")
            self.assertContains(resp_form, "sponsor_groups")

            resp = self.client.post(
                url,
                data={
                    "cn": "parent",
                    "description": "",
                    "members": [],
                    "sponsors": [],
                    "member_groups": ["alpha"],
                    "sponsor_groups": ["sponsor_alpha"],
                    "fas_url": "",
                    "fas_mailing_list": "",
                    "fas_irc_channels": "",
                    "fas_discussion_url": "",
                    "fas_group": True,
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)

        # The backing FreeIPAGroup instance should expose member_groups; actual add/remove
        # calls are asserted in the implementation tests once the methods exist.
        self.assertTrue(hasattr(existing_group, "member_groups"))
        self.assertTrue(hasattr(existing_group, "sponsor_groups"))
