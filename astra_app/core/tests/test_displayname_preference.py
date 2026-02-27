
from django.test import TestCase

from core.freeipa.user import FreeIPAUser


class DisplayNamePreferenceTests(TestCase):
    def test_full_name_prefers_freeipa_displayname_when_present(self) -> None:
        user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice CN"],
                "displayname": ["Alice Display"],
                "memberof_group": [],
            },
        )

        self.assertEqual(user.full_name, "Alice Display")

    def test_full_name_falls_back_to_gecos_when_no_displayname(self) -> None:
        user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "gecos": ["Alice Gecos"],
                "cn": ["Alice CN"],
                "memberof_group": [],
            },
        )

        self.assertEqual(user.full_name, "Alice Gecos")

    def test_full_name_falls_back_to_common_name_when_no_displayname_or_gecos(self) -> None:
        user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice CN"],
                "memberof_group": [],
            },
        )

        self.assertEqual(user.full_name, "Alice CN")

    def test_full_name_falls_back_to_first_and_last_name(self) -> None:
        user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        self.assertEqual(user.full_name, "Alice User")

