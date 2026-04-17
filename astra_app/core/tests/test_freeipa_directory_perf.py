from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from core.freeipa_directory import search_freeipa_users, snapshot_freeipa_users


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
    def test_snapshot_uses_lightweight_full_directory_shape_and_normalizes_rows(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["Bob"],
                        "gecos": ["Bob Example"],
                        "mail": ["bob@example.org"],
                        "fasIsPrivate": ["TRUE"],
                    },
                    {
                        "uid": ["alice"],
                        "displayname": ["Alice Example"],
                        "mail": ["alice@example.org"],
                        "memberof_group": ["packagers"],
                    },
                    {
                        "uid": ["skipme"],
                        "displayname": ["Skip Me"],
                    },
                    {
                        "uid": [""],
                        "displayname": ["Missing Username"],
                    },
                    {
                        "cn": ["No Username"],
                    },
                    {
                        "uid": ["carol"],
                        "cn": ["Carol Common"],
                    },
                    {
                        "uid": ["dave"],
                        "givenname": ["Dave"],
                        "sn": ["User"],
                    },
                ]
            }
        )

        with (
            patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client),
            patch(
                "core.freeipa_directory.FreeIPAUser.all",
                side_effect=AssertionError("Shape E must not fall back to FreeIPAUser.all()"),
            ),
            self.settings(FREEIPA_FILTERED_USERNAMES=("skipme",)),
        ):
            users = snapshot_freeipa_users()

        self.assertEqual([user.username for user in users], ["alice", "Bob", "carol", "dave"])
        self.assertEqual(users[0].full_name, "Alice Example")
        self.assertEqual(users[0].email, "alice@example.org")
        self.assertEqual(users[0].groups_list, [])
        self.assertEqual(users[1].full_name, "Bob Example")
        self.assertTrue(users[1].fas_is_private)
        self.assertEqual(users[2].full_name, "Carol Common")
        self.assertEqual(users[3].full_name, "Dave User")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            client.calls[0][1],
            {
                "o_all": False,
                "o_no_members": True,
                "o_sizelimit": 0,
                "o_timelimit": 0,
            },
        )

    def test_snapshot_does_not_touch_full_detail_cache(self) -> None:
        client = _DummyUserFindClient(
            {
                "result": [
                    {
                        "uid": ["alice"],
                        "displayname": ["Alice Example"],
                    }
                ]
            }
        )

        with (
            patch("core.freeipa_directory.FreeIPAUser.get_client", return_value=client),
            patch.object(cache, "get", side_effect=AssertionError("Shape E must not read the full-detail cache")),
            patch.object(cache, "set", side_effect=AssertionError("Shape E must not write the full-detail cache")),
            patch.object(cache, "get_or_set", side_effect=AssertionError("Shape E must not prime cache aliases")),
            patch.object(cache, "delete", side_effect=AssertionError("Shape E must not invalidate the full-detail cache")),
        ):
            users = snapshot_freeipa_users()

        self.assertEqual([user.username for user in users], ["alice"])

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
        self.assertEqual(
            client.calls[0][1],
            {
                "a_criteria": "alice",
                "o_all": False,
                "o_no_members": True,
                "o_sizelimit": 10,
                "o_timelimit": 0,
            },
        )

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
        self.assertEqual(
            client.calls[0][1],
            {
                "a_criteria": "user",
                "o_all": False,
                "o_no_members": True,
                "o_sizelimit": 11,
                "o_timelimit": 0,
            },
        )

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
        self.assertEqual(
            client.calls[0][1],
            {
                "a_criteria": "smith",
                "o_all": False,
                "o_no_members": True,
                "o_sizelimit": 10,
                "o_timelimit": 0,
            },
        )

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
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            client.calls[0][1],
            {
                "a_criteria": "alex ir",
                "o_all": False,
                "o_no_members": True,
                "o_sizelimit": 10,
                "o_timelimit": 0,
            },
        )
        self.assertEqual(
            client.calls[1][1],
            {
                "a_criteria": "alex",
                "o_all": False,
                "o_no_members": True,
                "o_sizelimit": 10,
                "o_timelimit": 0,
            },
        )
