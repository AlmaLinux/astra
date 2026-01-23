from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAUser, clear_current_viewer_username, set_current_viewer_username
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_VIEW_USER_DIRECTORY


class GlobalSearchTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_search_requires_login(self) -> None:
        resp = self.client.get("/search/?q=a")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.headers.get("Location", ""))

    def test_search_returns_users_and_groups(self) -> None:
        self._login_as_freeipa("admin")

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_USER_DIRECTORY,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
        )

        users = [
            SimpleNamespace(username="jim", full_name="Jim Jones"),
            SimpleNamespace(username="jimbo", full_name="Jimbo Jones"),
            SimpleNamespace(username="bob", full_name="Bob User"),
        ]

        groups = [
            SimpleNamespace(cn="example-jin", description="", fas_group=True),
            SimpleNamespace(cn="gitdocker-example", description="", fas_group=True),
            SimpleNamespace(cn="ipa_only", description="", fas_group=False),
        ]

        with (
            patch("core.backends.FreeIPAUser.all", return_value=users),
            patch("core.backends.FreeIPAGroup.all", return_value=groups),
        ):
            resp = self.client.get("/search/?q=ji")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual([u["username"] for u in data["users"]], ["jim", "jimbo"])
        self.assertNotIn("bob", {u["username"] for u in data["users"]})
        self.assertEqual([g["cn"] for g in data["groups"]], ["example-jin"])
        self.assertNotIn("ipa_only", {g["cn"] for g in data["groups"]})

    def test_search_empty_query_returns_empty_results(self) -> None:
        self._login_as_freeipa("admin")

        with (
            patch("core.backends.FreeIPAUser.all", return_value=[SimpleNamespace(username="alice", full_name="")]),
            patch("core.backends.FreeIPAGroup.all", return_value=[SimpleNamespace(cn="fas1", description="", fas_group=True)]),
        ):
            resp = self.client.get("/search/?q=")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"users": [], "groups": []})

    def test_search_does_not_match_private_user_by_full_name(self) -> None:
        self._login_as_freeipa("admin")

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_USER_DIRECTORY,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.org"],
                "fasIsPrivate": ["FALSE"],
            },
        )
        set_current_viewer_username("admin")
        try:
            bob_private = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasIsPrivate": ["TRUE"],
                },
            )
        finally:
            clear_current_viewer_username()

        with (
            patch("core.backends.FreeIPAUser.all", return_value=[alice, bob_private]),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get("/search/?q=User")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("alice", {u["username"] for u in data["users"]})
        self.assertNotIn("bob", {u["username"] for u in data["users"]})


class GlobalSearchTemplateCopyTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_placeholder_and_aria_without_directory_access(self) -> None:
        self._login_as_freeipa("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get("/groups/")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertRegex(
            html,
            re.compile(
                r"id=\"global-search-input\".*?placeholder=\"Search groups\.\.\.\".*?aria-label=\"Search groups\"",
                re.S,
            ),
        )

    def test_placeholder_and_aria_with_directory_access(self) -> None:
        self._login_as_freeipa("admin")
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_USER_DIRECTORY,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=admin):
            resp = self.client.get("/groups/")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertRegex(
            html,
            re.compile(
                r"id=\"global-search-input\".*?placeholder=\"Search users and groups\.\.\.\".*?aria-label=\"Search users and groups\"",
                re.S,
            ),
        )
