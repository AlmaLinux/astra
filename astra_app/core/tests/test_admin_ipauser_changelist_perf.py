from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class _DummyUserFindClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def user_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append((args, kwargs))
        return self.result


class AdminIPAUserChangelistPerformanceTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_changelist_uses_server_side_user_find_without_loading_all_users(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["bob"],
                        "displayname": ["Bob Example"],
                        "givenname": ["Bob"],
                        "sn": ["Example"],
                        "mail": ["bob@example.org"],
                    }
                ]
            }
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.get_client", return_value=client),
            patch(
                "core.admin.FreeIPAUser.all",
                side_effect=AssertionError("admin changelist should not load the full FreeIPA directory"),
            ),
        ):
            response = self.client.get(reverse("admin:auth_ipauser_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bob")
        self.assertEqual(len(client.calls), 1)