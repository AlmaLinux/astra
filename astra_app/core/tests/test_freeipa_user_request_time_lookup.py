from collections.abc import Callable
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from core.freeipa.user import FreeIPAUser
from core.freeipa.utils import _user_cache_key
from core.freeipa_directory import search_freeipa_users


class _ShapeLookupClient:
    def __init__(
        self,
        *,
        search_results: dict[str, dict[str, object]] | None = None,
        username_results: dict[str, dict[str, object]] | None = None,
        email_results: dict[str, dict[str, object]] | None = None,
        user_show_results: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.search_results = dict(search_results or {})
        self.username_results = dict(username_results or {})
        self.email_results = dict(email_results or {})
        self.user_show_results = dict(user_show_results or {})
        self.user_find_calls: list[dict[str, object]] = []
        self.user_show_calls: list[tuple[str, dict[str, object]]] = []

    def user_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        self.user_find_calls.append(dict(kwargs))

        email = str(kwargs.get("o_mail") or "").strip().lower()
        if email:
            return self.email_results.get(email, {"count": 0, "result": []})

        username = str(kwargs.get("o_uid") or "").strip().lower()
        if username:
            return self.username_results.get(username, {"count": 0, "result": []})

        criteria = str(kwargs.get("a_criteria") or "").strip().lower()
        return self.search_results.get(criteria, {"result": []})

    def user_show(self, username: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        self.user_show_calls.append((username, dict(kwargs)))
        return {"result": self.user_show_results[username]}


def _lightweight_row(username: str, *, full_name: str | None = None) -> dict[str, object]:
    display_name = full_name or username
    return {
        "uid": [username],
        "displayname": [display_name],
        "mail": [f"{username}@example.com"],
    }


def _full_detail_row(username: str) -> dict[str, object]:
    return {
        "uid": [username],
        "givenname": ["Alice"],
        "sn": ["Example"],
        "displayname": ["Alice Example"],
        "mail": [f"{username}@example.com"],
        "memberof_group": ["packagers"],
        "fasIsPrivate": ["FALSE"],
    }


class FreeIPAUserRequestTimeLookupTests(TestCase):
    def tearDown(self) -> None:
        cache.delete(_user_cache_key("alice"))
        super().tearDown()

    def test_find_by_email_uses_lightweight_request_time_shape(self) -> None:
        client = _ShapeLookupClient(
            email_results={
                "alice@example.com": {
                    "count": 1,
                    "result": [_lightweight_row("alice", full_name="Alice Example")],
                }
            }
        )

        with patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client):
            user = FreeIPAUser.find_by_email("  ALICE@example.com ")

        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.username, "alice")
        self.assertEqual(user.full_name, "Alice Example")
        self.assertEqual(
            client.user_find_calls,
            [
                {
                    "o_mail": "alice@example.com",
                    "o_all": False,
                    "o_no_members": True,
                    "o_sizelimit": 1,
                    "o_timelimit": 0,
                }
            ],
        )

    def test_find_by_email_returns_none_for_blank_empty_results_and_missing_uid(self) -> None:
        missing_uid_client = _ShapeLookupClient(
            email_results={
                "alice@example.com": {
                    "count": 1,
                    "result": [{"displayname": ["Alice Example"]}],
                }
            }
        )

        self.assertIsNone(FreeIPAUser.find_by_email(""))

        with patch("core.freeipa.user.FreeIPAUser.get_client", return_value=_ShapeLookupClient()):
            self.assertIsNone(FreeIPAUser.find_by_email("alice@example.com"))

        with patch("core.freeipa.user.FreeIPAUser.get_client", return_value=missing_uid_client):
            self.assertIsNone(FreeIPAUser.find_by_email("alice@example.com"))

    def test_find_lightweight_by_usernames_deduplicates_and_uses_exact_shape(self) -> None:
        client = _ShapeLookupClient(
            username_results={
                "alice": {"count": 1, "result": [_lightweight_row("alice", full_name="Alice Example")]},
                "bob": {"count": 1, "result": [_lightweight_row("bob", full_name="Bob Example")]},
            }
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client),
            patch("core.freeipa.user.FreeIPAUser.all", side_effect=AssertionError("lightweight username lookup must not fall back to FreeIPAUser.all")),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=AssertionError("lightweight username lookup must not fall back to FreeIPAUser.get")),
        ):
            users_by_username = FreeIPAUser.find_lightweight_by_usernames([" Alice ", "alice", "bob", ""])

        self.assertEqual(set(users_by_username), {"alice", "bob"})
        self.assertEqual(users_by_username["alice"].full_name, "Alice Example")
        self.assertEqual(users_by_username["bob"].full_name, "Bob Example")
        self.assertEqual(
            client.user_find_calls,
            [
                {
                    "o_uid": "alice",
                    "o_all": False,
                    "o_no_members": True,
                    "o_sizelimit": 1,
                    "o_timelimit": 0,
                },
                {
                    "o_uid": "bob",
                    "o_all": False,
                    "o_no_members": True,
                    "o_sizelimit": 1,
                    "o_timelimit": 0,
                },
            ],
        )

    def test_find_lightweight_by_usernames_omits_missing_usernames(self) -> None:
        client = _ShapeLookupClient(
            username_results={
                "alice": {"count": 1, "result": [_lightweight_row("alice", full_name="Alice Example")]},
                "ghost": {"count": 0, "result": []},
            }
        )

        with patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client):
            users_by_username = FreeIPAUser.find_lightweight_by_usernames(["alice", "ghost"])

        self.assertEqual(set(users_by_username), {"alice"})

    def test_shape_a_search_does_not_touch_full_detail_cache(self) -> None:
        client = _ShapeLookupClient(
            search_results={
                "alice": {
                    "result": [_lightweight_row("alice", full_name="Alice Example")],
                }
            }
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client),
            patch.object(cache, "get", side_effect=AssertionError("search helper must not read the full-detail cache")),
            patch.object(cache, "set", side_effect=AssertionError("search helper must not write the full-detail cache")),
            patch.object(cache, "get_or_set", side_effect=AssertionError("search helper must not prime cache aliases")),
            patch.object(cache, "delete", side_effect=AssertionError("search helper must not invalidate the full-detail cache")),
        ):
            users = search_freeipa_users(query="alice", limit=10)

        self.assertEqual([user.username for user in users], ["alice"])

    def test_shape_b_lightweight_username_lookup_does_not_touch_full_detail_cache(self) -> None:
        client = _ShapeLookupClient(
            username_results={
                "alice": {"count": 1, "result": [_lightweight_row("alice", full_name="Alice Example")]},
            }
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client),
            patch.object(cache, "get", side_effect=AssertionError("shape B must not read the full-detail cache")),
            patch.object(cache, "set", side_effect=AssertionError("shape B must not write the full-detail cache")),
            patch.object(cache, "get_or_set", side_effect=AssertionError("shape B must not prime cache aliases")),
            patch.object(cache, "delete", side_effect=AssertionError("shape B must not invalidate the full-detail cache")),
        ):
            users_by_username = FreeIPAUser.find_lightweight_by_usernames(["alice"])

        self.assertEqual(set(users_by_username), {"alice"})

    def test_shape_c_lightweight_email_lookup_does_not_touch_full_detail_cache(self) -> None:
        client = _ShapeLookupClient(
            email_results={
                "alice@example.com": {
                    "count": 1,
                    "result": [_lightweight_row("alice", full_name="Alice Example")],
                }
            }
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client),
            patch.object(cache, "get", side_effect=AssertionError("shape C must not read the full-detail cache")),
            patch.object(cache, "set", side_effect=AssertionError("shape C must not write the full-detail cache")),
            patch.object(cache, "get_or_set", side_effect=AssertionError("shape C must not prime cache aliases")),
            patch.object(cache, "delete", side_effect=AssertionError("shape C must not invalidate the full-detail cache")),
        ):
            user = FreeIPAUser.find_by_email("alice@example.com")

        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.username, "alice")

    def test_shape_a_search_lookup_does_not_satisfy_later_full_detail_get(self) -> None:
        self._assert_later_get_fetches_full_detail(
            lambda: search_freeipa_users(query="alice", limit=10),
            client=_ShapeLookupClient(
                search_results={
                    "alice": {
                        "result": [_lightweight_row("alice", full_name="Alice Example")],
                    }
                },
                user_show_results={"alice": _full_detail_row("alice")},
            ),
        )

    def test_shape_b_username_lookup_does_not_satisfy_later_full_detail_get(self) -> None:
        self._assert_later_get_fetches_full_detail(
            lambda: FreeIPAUser.find_lightweight_by_usernames(["alice"]),
            client=_ShapeLookupClient(
                username_results={
                    "alice": {"count": 1, "result": [_lightweight_row("alice", full_name="Alice Example")]},
                },
                user_show_results={"alice": _full_detail_row("alice")},
            ),
        )

    def test_shape_c_email_lookup_does_not_satisfy_later_full_detail_get(self) -> None:
        self._assert_later_get_fetches_full_detail(
            lambda: FreeIPAUser.find_by_email("alice@example.com"),
            client=_ShapeLookupClient(
                email_results={
                    "alice@example.com": {
                        "count": 1,
                        "result": [_lightweight_row("alice", full_name="Alice Example")],
                    }
                },
                user_show_results={"alice": _full_detail_row("alice")},
            ),
        )

    def _assert_later_get_fetches_full_detail(
        self,
        helper_call: Callable[[], object],
        *,
        client: _ShapeLookupClient,
    ) -> None:
        cache.delete(_user_cache_key("alice"))

        with patch("core.freeipa.user.FreeIPAUser.get_client", return_value=client):
            helper_call()
            full_user = FreeIPAUser.get("alice")

        self.assertIsNotNone(full_user)
        assert full_user is not None
        self.assertEqual(full_user.full_name, "Alice Example")
        self.assertEqual(full_user.groups_list, ["packagers"])
        self.assertEqual(
            client.user_show_calls,
            [("alice", {"o_all": True, "o_no_members": False})],
        )