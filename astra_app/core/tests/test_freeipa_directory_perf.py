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


class _BranchingUserFindClient:
    def __init__(self, results_by_criteria: dict[str, dict[str, object]]) -> None:
        self.results_by_criteria = results_by_criteria
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def user_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append((args, kwargs))
        criteria = str(kwargs.get("a_criteria") or "")
        return self.results_by_criteria.get(criteria, {"result": []})


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

    def test_search_filters_out_server_matches_from_non_name_attributes(self) -> None:
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

        self.assertEqual([user.username for user in users], [])
        self.assertEqual(len(client.calls), 1)

    def test_search_matches_multiword_display_name_query_with_middle_name_gap(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["alice"],
                        "displayname": ["Alex Ivan Ramirez"],
                    }
                ]
            }
        )

        with patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client):
            users = search_freeipa_users(query="alex ir", limit=10)

        self.assertEqual([user.username for user in users], ["alice"])

    def test_search_does_not_match_near_miss_multiword_initials_query(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["alice"],
                        "displayname": ["Alex Ivan Ramirez"],
                    }
                ]
            }
        )

        with patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client):
            users = search_freeipa_users(query="lex ir", limit=10)

        self.assertEqual([user.username for user in users], [])

    def test_search_recovers_alex_ir_when_phrase_lookup_is_empty(self) -> None:
        client = _BranchingUserFindClient(
            {
                "alex ir": {"result": []},
                "alex": {
                    "result": [
                        {
                            "uid": ["alice"],
                            "displayname": ["Alex Ivan Ramirez"],
                        }
                    ]
                },
            }
        )

        with (
            patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client),
            patch(
                "core.freeipa_directory.FreeIPAUser.all",
                side_effect=AssertionError("full directory scan should not be used for search"),
            ),
        ):
            users = search_freeipa_users(query="alex ir", limit=10)

        self.assertEqual([user.username for user in users], ["alice"])
        self.assertEqual([call[1].get("a_criteria") for call in client.calls], ["alex ir", "alex"])
