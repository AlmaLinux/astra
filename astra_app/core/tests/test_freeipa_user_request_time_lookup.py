import threading
import time
from collections.abc import Callable
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from python_freeipa import exceptions

from core.freeipa.client import clear_freeipa_service_client_cache
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


class _LookupExecutionTracker:
    def __init__(
        self,
        *,
        transient_unauthorized_by_username: dict[str, int] | None = None,
        fatal_usernames: set[str] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.client_count = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self.lookup_counts_by_username: dict[str, int] = {}
        self.transient_unauthorized_by_username = dict(transient_unauthorized_by_username or {})
        self.fatal_usernames = set(fatal_usernames or set())

    def register_client(self) -> int:
        with self._lock:
            self.client_count += 1
            return self.client_count

    def begin_lookup(self, username: str) -> None:
        with self._lock:
            self.active_calls += 1
            if self.active_calls > self.max_active_calls:
                self.max_active_calls = self.active_calls
            self.lookup_counts_by_username[username] = self.lookup_counts_by_username.get(username, 0) + 1

    def end_lookup(self) -> None:
        with self._lock:
            self.active_calls -= 1

    def consume_transient_unauthorized(self, username: str) -> bool:
        with self._lock:
            remaining = self.transient_unauthorized_by_username.get(username, 0)
            if remaining <= 0:
                return False
            self.transient_unauthorized_by_username[username] = remaining - 1
            return True


class _TrackingShapeLookupClient(_ShapeLookupClient):
    def __init__(
        self,
        *,
        tracker: _LookupExecutionTracker,
        username_results: dict[str, dict[str, object]],
        pause_seconds: float = 0.0,
    ) -> None:
        super().__init__(username_results=username_results)
        self.tracker = tracker
        self.pause_seconds = pause_seconds

    def user_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        username = str(kwargs.get("o_uid") or "").strip().lower()
        self.tracker.begin_lookup(username)
        try:
            if self.pause_seconds:
                time.sleep(self.pause_seconds)
            if username in self.tracker.fatal_usernames:
                raise RuntimeError(f"boom for {username}")
            if self.tracker.consume_transient_unauthorized(username):
                raise exceptions.Unauthorized()
            return super().user_find(*args, **kwargs)
        finally:
            self.tracker.end_lookup()


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
        self.assertCountEqual(
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

    def test_find_lightweight_by_usernames_uses_serial_fast_path_for_small_inputs(self) -> None:
        tracker = _LookupExecutionTracker()

        def build_client() -> _TrackingShapeLookupClient:
            tracker.register_client()
            return _TrackingShapeLookupClient(
                tracker=tracker,
                username_results={
                    "alice": {"count": 1, "result": [_lightweight_row("alice", full_name="Alice Example")]},
                    "bob": {"count": 1, "result": [_lightweight_row("bob", full_name="Bob Example")]},
                },
                pause_seconds=0.01,
            )

        with (
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_SERIAL_THRESHOLD", 2, create=True),
            patch("core.freeipa.user.FreeIPAUser.get_client", side_effect=build_client),
        ):
            users_by_username = FreeIPAUser.find_lightweight_by_usernames(["alice", "bob"])

        self.assertEqual(set(users_by_username), {"alice", "bob"})
        self.assertEqual(tracker.client_count, 1)
        self.assertEqual(tracker.max_active_calls, 1)

    def test_find_lightweight_by_usernames_bounds_service_client_fanout(self) -> None:
        tracker = _LookupExecutionTracker()
        usernames = [f"user{index}" for index in range(8)]
        max_workers = 3
        username_results = {
            username: {"count": 1, "result": [_lightweight_row(username, full_name=f"{username} Example")]}
            for username in usernames
        }

        def build_client(*_args: object, **_kwargs: object) -> _TrackingShapeLookupClient:
            tracker.register_client()
            return _TrackingShapeLookupClient(
                tracker=tracker,
                username_results=username_results,
                pause_seconds=0.02,
            )

        with (
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_SERIAL_THRESHOLD", 2, create=True),
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_CHUNK_SIZE", 2, create=True),
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_MAX_WORKERS", max_workers, create=True),
            patch("core.freeipa.client._get_freeipa_client", side_effect=build_client),
        ):
            clear_freeipa_service_client_cache()
            users_by_username = FreeIPAUser.find_lightweight_by_usernames(usernames)
            clear_freeipa_service_client_cache()

        self.assertEqual(set(users_by_username), set(usernames))
        self.assertGreater(tracker.client_count, 1)
        self.assertLessEqual(tracker.client_count, max_workers)
        self.assertGreater(tracker.max_active_calls, 1)

    def test_find_lightweight_by_usernames_retries_unauthorized_within_worker_chunk(self) -> None:
        tracker = _LookupExecutionTracker(transient_unauthorized_by_username={"charlie": 1})
        usernames = ["alice", "bob", "charlie", "dave"]
        username_results = {
            username: {"count": 1, "result": [_lightweight_row(username, full_name=f"{username} Example")]}
            for username in usernames
        }

        def build_client() -> _TrackingShapeLookupClient:
            tracker.register_client()
            return _TrackingShapeLookupClient(tracker=tracker, username_results=username_results)

        with (
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_SERIAL_THRESHOLD", 2, create=True),
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_CHUNK_SIZE", 2, create=True),
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_MAX_WORKERS", 2, create=True),
            patch("core.freeipa.user.FreeIPAUser.get_client", side_effect=build_client),
        ):
            users_by_username = FreeIPAUser.find_lightweight_by_usernames(usernames)

        self.assertEqual(set(users_by_username), set(usernames))
        self.assertEqual(tracker.lookup_counts_by_username.get("alice"), 1)
        self.assertEqual(tracker.lookup_counts_by_username.get("bob"), 1)
        self.assertEqual(tracker.lookup_counts_by_username.get("charlie"), 2)
        self.assertEqual(tracker.lookup_counts_by_username.get("dave"), 1)

    def test_find_lightweight_by_usernames_returns_empty_dict_when_any_worker_fails(self) -> None:
        tracker = _LookupExecutionTracker(fatal_usernames={"charlie"})
        usernames = ["alice", "bob", "charlie", "dave"]
        username_results = {
            username: {"count": 1, "result": [_lightweight_row(username, full_name=f"{username} Example")]}
            for username in usernames
        }

        def build_client() -> _TrackingShapeLookupClient:
            tracker.register_client()
            return _TrackingShapeLookupClient(tracker=tracker, username_results=username_results)

        with (
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_SERIAL_THRESHOLD", 2, create=True),
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_CHUNK_SIZE", 2, create=True),
            patch("core.freeipa.user._LIGHTWEIGHT_LOOKUP_MAX_WORKERS", 2, create=True),
            patch("core.freeipa.user.FreeIPAUser.get_client", side_effect=build_client),
        ):
            users_by_username = FreeIPAUser.find_lightweight_by_usernames(usernames)

        self.assertEqual(users_by_username, {})

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