
from django.test import TestCase

from core.backends import DegradedFreeIPAUser


class DegradedFreeIPAUserTests(TestCase):
    def test_degraded_user_exposes_safe_defaults(self) -> None:
        user = DegradedFreeIPAUser("alice")

        self.assertTrue(user.is_authenticated)
        self.assertFalse(user.is_anonymous)
        self.assertEqual(user.get_username(), "alice")
        self.assertEqual(user.username, "alice")
        self.assertEqual(user.email, "")
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")
        self.assertEqual(user.displayname, "")
        self.assertEqual(user.fasstatusnote, "")
        self.assertEqual(user.groups_list, [])
        self.assertEqual(user.timezone, "")

        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm("core.view_user"))
        self.assertFalse(user.has_perms(["core.view_user"]))
        self.assertEqual(user.get_all_permissions(), set())
