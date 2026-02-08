
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from core.backends import FreeIPAUser
from core.views_auth import FreeIPALoginView


class UserRoutesTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_user_profile_route_renders(self) -> None:
        username = "admin"
        self._login_as_freeipa(username)

        fu = FreeIPAUser(username, {"uid": [username], "givenname": ["A"], "sn": ["Dmin"], "mail": ["a@example.org"]})

        with patch("core.backends.FreeIPAUser.get", return_value=fu):
            resp = self.client.get(f"/user/{username}/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn(username, resp.content.decode("utf-8"))

    def test_users_list_route_renders(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            SimpleNamespace(username="alice", get_full_name=lambda: "Alice User"),
            SimpleNamespace(username="bob", get_full_name=lambda: "Bob User"),
        ]

        with patch("core.backends.FreeIPAUser.all", return_value=users):
            resp = self.client.get("/users/")

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("alice", content)
        self.assertIn("bob", content)

    def test_users_list_paginates_30_per_page(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            SimpleNamespace(username=f"user{i:03d}", get_full_name=lambda: "")
            for i in range(65)
        ]

        with patch("core.backends.FreeIPAUser.all", return_value=users):
            resp_page_1 = self.client.get("/users/")
            resp_page_2 = self.client.get("/users/?page=2")

        self.assertEqual(resp_page_1.status_code, 200)
        self.assertContains(resp_page_1, 'href="/user/user000/"')
        self.assertContains(resp_page_1, 'href="/user/user027/"')
        self.assertNotContains(resp_page_1, 'href="/user/user028/"')

        self.assertEqual(resp_page_2.status_code, 200)
        self.assertContains(resp_page_2, 'href="/user/user028/"')

    def test_users_list_search_filters_results(self) -> None:
        self._login_as_freeipa("admin")

        users = [
            SimpleNamespace(username="alice", get_full_name=lambda: "Alice User"),
            SimpleNamespace(username="bob", get_full_name=lambda: "Bob User"),
        ]

        with patch("core.backends.FreeIPAUser.all", return_value=users):
            resp = self.client.get("/users/?q=ali")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/user/alice/"')
        self.assertNotContains(resp, 'href="/user/bob/"')


class LoginRedirectTests(TestCase):
    def test_freeipa_login_view_redirects_to_canonical_user_profile_url(self) -> None:
        factory = RequestFactory()
        request = factory.get("/login/")
        setattr(request, "user", cast(Any, SimpleNamespace(get_username=lambda: "alice")))

        view = FreeIPALoginView()
        view.request = request

        self.assertEqual(view.get_success_url(), "/user/alice/")
