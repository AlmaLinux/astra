
from django.test import TestCase

from core.freeipa.user import FreeIPAUser


class FreeIPAUserIndirectGroupsTests(TestCase):
    def test_groups_list_includes_indirect_groups(self) -> None:
        user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "memberof_group": ["direct"],
                "memberofindirect_group": ["indirect", "direct"],
            },
        )

        self.assertEqual(user.direct_groups_list, ["direct"])
        self.assertEqual(user.indirect_groups_list, ["indirect", "direct"])
        self.assertEqual(user.groups_list, ["direct", "indirect"])
