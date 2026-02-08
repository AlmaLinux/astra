
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAGroup


class GroupDetailInfoRowsHiddenTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_hides_empty_info_rows(self) -> None:
        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "parent",
            {
                "cn": ["parent"],
                "description": [""],
                "member_user": [],
                "member_group": [],
                "membermanager_user": [],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )

        with patch("core.backends.FreeIPAGroup.get", return_value=group):
            resp = self.client.get("/group/parent/")

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Mailing list")
        self.assertNotContains(resp, "IRC channels")
        self.assertNotContains(resp, "Discussion URL")
        self.assertNotContains(resp, "URL")
