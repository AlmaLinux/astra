from unittest.mock import patch

from django.test import TestCase

from core.freeipa_directory import search_freeipa_users


class _DummyUserFindClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def user_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append((args, kwargs))
        return self.result


class FreeIPADirectoryPerformanceTests(TestCase):
    def test_search_uses_server_side_user_find_not_full_directory_scan(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["alice"],
                        "givenname": ["Alice"],
                        "sn": ["User"],
                        "displayname": ["Alice User"],
                        "mail": ["alice@example.org"],
                    }
                ]
            }
        )

        with (
            patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client),
            patch(
                "core.freeipa_directory.FreeIPAUser.all",
                side_effect=AssertionError("full directory scan should not be used for search"),
            ),
        ):
            users = search_freeipa_users(query="alice", limit=10)

        self.assertEqual([user.username for user in users], ["alice"])
        self.assertEqual(len(client.calls), 1)

    def test_search_applies_exclusions_without_fallback_to_all(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["alice"],
                        "displayname": ["Alice User"],
                    },
                    {
                        "uid": ["bob"],
                        "displayname": ["Bob User"],
                    },
                ]
            }
        )

        with (
            patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client),
            patch(
                "core.freeipa_directory.FreeIPAUser.all",
                side_effect=AssertionError("full directory scan should not be used for search"),
            ),
        ):
            users = search_freeipa_users(query="user", limit=10, exclude_usernames={"bob"})

        self.assertEqual([user.username for user in users], ["alice"])
        self.assertEqual(len(client.calls), 1)

    def test_search_trusts_server_matches_from_non_name_attributes(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["john"],
                        "displayname": ["John D."],
                        "mail": ["john@smith.com"],
                    }
                ]
            }
        )

        with (
            patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client),
            patch(
                "core.freeipa_directory.FreeIPAUser.all",
                side_effect=AssertionError("full directory scan should not be used for search"),
            ),
        ):
            users = search_freeipa_users(query="smith", limit=10)

        self.assertEqual([user.username for user in users], ["john"])
        self.assertEqual(len(client.calls), 1)