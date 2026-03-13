
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings

from core.freeipa.user import FreeIPAUser
from core.views_auth import FreeIPALoginView


class UserRoutesTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _user_stub(self, username: str, full_name: str = "", email: str = "") -> SimpleNamespace:
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            username=username,
            email=email,
            get_full_name=lambda: full_name,
        )

    def test_user_profile_route_renders(self) -> None:
        username = "admin"
        self._login_as_freeipa(username)

        fu = FreeIPAUser(username, {"uid": [username], "givenname": ["A"], "sn": ["Dmin"], "mail": ["a@example.org"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=fu):
            resp = self.client.get(f"/user/{username}/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn(username, resp.content.decode("utf-8"))

    def test_users_list_route_renders(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            self._user_stub("alice", full_name="Alice User", email="alice@example.org"),
            self._user_stub("bob", full_name="Bob User", email="bob@example.org"),
        ]

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=users):
            resp = self.client.get("/users/")

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Loading users", content)
        self.assertIn("data-users-grid-url", content)
        self.assertNotIn('href="/user/alice/"', content)

    def test_users_grid_paginates_30_per_page(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            self._user_stub(f"user{i:03d}", email=f"user{i:03d}@example.org")
            for i in range(65)
        ]

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=users):
            resp_page_1 = self.client.get("/users/grid/")
            resp_page_2 = self.client.get("/users/grid/?page=2")

        self.assertEqual(resp_page_1.status_code, 200)
        payload_page_1 = json.loads(resp_page_1.content)
        payload_page_2 = json.loads(resp_page_2.content)

        usernames_page_1 = [item["username"] for item in payload_page_1.get("users", [])]
        usernames_page_2 = [item["username"] for item in payload_page_2.get("users", [])]

        self.assertIn("user000", usernames_page_1)
        self.assertIn("user027", usernames_page_1)
        self.assertNotIn("user028", usernames_page_1)
        self.assertIn("user028", usernames_page_2)

    def test_users_grid_search_filters_results(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            self._user_stub("alice", full_name="Alice User", email="alice@example.org"),
            self._user_stub("bob", full_name="Bob User", email="bob@example.org"),
        ]

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=users):
            resp = self.client.get("/users/grid/?q=ali")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        usernames = [item["username"] for item in payload.get("users", [])]
        self.assertIn("alice", usernames)
        self.assertNotIn("bob", usernames)

    @override_settings(
        AVATAR_PROVIDERS=(
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        )
    )
    def test_users_grid_minimal_payload(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            self._user_stub("alice", full_name="Alice User", email="alice@example.org"),
        ]

        with patch("core.freeipa.user.FreeIPAUser.all", return_value=users):
            resp = self.client.get("/users/grid/")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        items = payload.get("users", [])
        self.assertEqual(1, len(items))
        self.assertEqual({"username", "full_name", "avatar_url"}, set(items[0].keys()))
        self.assertEqual("alice", items[0]["username"])
        self.assertEqual("Alice User", items[0]["full_name"])
        self.assertIn("gravatar.com/avatar", items[0]["avatar_url"])


class LoginRedirectTests(TestCase):
    def test_freeipa_login_view_redirects_to_canonical_user_profile_url(self) -> None:
        factory = RequestFactory()
        request = factory.get("/login/")
        setattr(request, "user", cast(Any, SimpleNamespace(get_username=lambda: "alice")))

        view = FreeIPALoginView()
        view.request = request

        self.assertEqual(view.get_success_url(), "/user/alice/")
