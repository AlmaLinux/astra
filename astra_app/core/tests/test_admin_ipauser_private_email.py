
from html.parser import HTMLParser
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class _FreeIPAHeadingPlacementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._stack: list[tuple[str, dict[str, str]]] = []
        self.heading_found = False
        self.heading_in_sidebar = False
        self.heading_in_lower_row = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict: dict[str, str] = {k: v for k, v in attrs if v is not None}
        self._stack.append((tag, attr_dict))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self.heading_found or "FreeIPA Attributes" not in data:
            return
        self.heading_found = True
        self.heading_in_sidebar = any(
            attrs.get("id") == "jazzy-actions" or "col-lg-3" in (attrs.get("class") or "")
            for _, attrs in self._stack
        )
        self.heading_in_lower_row = any(attrs.get("id") == "freeipa-attributes-row" for _, attrs in self._stack)


class AdminIPAUserPrivateEmailTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "admin") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_change_form_shows_email_for_private_user(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return FreeIPAUser(
                    "sej7278",
                    {
                        "uid": ["sej7278"],
                        "mail": ["sej7278@example.org"],
                        "fasIsPrivate": ["TRUE"],
                        "memberof_group": [],
                    },
                )
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="sej7278@example.org"')

    def test_change_form_shows_all_available_freeipa_attributes(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return FreeIPAUser(
                    "sej7278",
                    {
                        "uid": ["sej7278"],
                        "mail": ["sej7278@example.org"],
                        "telephonenumber": ["+1-555-0100"],
                        "fasstatusnote": ["Temporary contractor"],
                        "memberof_group": ["packager"],
                        "memberofindirect_group": ["cla_done"],
                    },
                )
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "FreeIPA Attributes")
        self.assertContains(resp, "telephonenumber")
        self.assertContains(resp, "+1-555-0100")
        self.assertContains(resp, "fasstatusnote")
        self.assertContains(resp, "Temporary contractor")

    def test_change_form_escapes_rendered_freeipa_attribute_values(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})
        dangerous_value = '<img src=x onerror="alert(1)">'
        target_user = FreeIPAUser(
            "sej7278",
            {
                "uid": ["sej7278"],
                "mail": ["sej7278@example.org"],
                "fasstatusnote": [dangerous_value],
                "memberof_group": ["packager"],
            },
        )

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;")
        self.assertNotContains(resp, dangerous_value)

    def test_change_form_renders_multivalued_freeipa_attributes_as_comma_separated_text(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return FreeIPAUser(
                    "sej7278",
                    {
                        "uid": ["sej7278"],
                        "mail": ["sej7278@example.org"],
                        "ipaSshPubKey": ["ssh-ed25519 AAAA", "ssh-rsa BBBB"],
                        "memberof_group": ["packager", "  ", "docs"],
                    },
                )
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ipaSshPubKey")
        self.assertContains(resp, "ssh-ed25519 AAAA, ssh-rsa BBBB")
        self.assertContains(resp, "memberof_group")
        self.assertContains(resp, "packager, docs")
        self.assertNotContains(resp, "['ssh-ed25519 AAAA', 'ssh-rsa BBBB']")

    def test_change_form_renders_save_controls_before_freeipa_attributes_table(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return FreeIPAUser(
                    "sej7278",
                    {
                        "uid": ["sej7278"],
                        "mail": ["sej7278@example.org"],
                        "telephonenumber": ["+1-555-0100"],
                        "memberof_group": ["packager"],
                    },
                )
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        response_html = resp.content.decode("utf-8")
        save_controls_index = response_html.find('name="_save"')
        freeipa_attributes_index = response_html.find("FreeIPA Attributes")

        self.assertNotEqual(save_controls_index, -1)
        self.assertNotEqual(freeipa_attributes_index, -1)
        self.assertLess(save_controls_index, freeipa_attributes_index)

    def test_change_form_renders_freeipa_attributes_in_lower_row_not_sidebar(self) -> None:
        self._login_as_freeipa_admin("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": ["admins"]})

        def _fake_user_get(username: str):
            if username == "admin":
                return admin_user
            if username == "sej7278":
                return FreeIPAUser(
                    "sej7278",
                    {
                        "uid": ["sej7278"],
                        "mail": ["sej7278@example.org"],
                        "telephonenumber": ["+1-555-0100"],
                        "memberof_group": ["packager"],
                    },
                )
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.admin.FreeIPAGroup.all", return_value=[]),
        ):
            url = reverse("admin:auth_ipauser_change", args=["sej7278"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        parser = _FreeIPAHeadingPlacementParser()
        parser.feed(resp.content.decode("utf-8"))

        self.assertTrue(parser.heading_found)
        self.assertFalse(parser.heading_in_sidebar)
        self.assertTrue(parser.heading_in_lower_row)
