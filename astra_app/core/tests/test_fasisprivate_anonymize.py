from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase

from core import views_users
from core.backends import FreeIPAUser, clear_current_viewer_username, set_current_viewer_username


class FASIsPrivateAnonymizeTests(TestCase):
    def test_private_user_anonymizes_when_attr_is_fasisprivate(self) -> None:
        set_current_viewer_username("alice")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasPronoun": ["they/them"],
                    "fasisprivate": ["TRUE"],
                },
            )
        finally:
            clear_current_viewer_username()

        self.assertEqual(bob.full_name, "bob")
        self.assertNotIn("fasPronoun", bob._user_data)

    def test_private_user_cache_is_not_poisoned_by_other_viewer(self) -> None:
        # Store full user data in the Django cache under the same key used by
        # FreeIPAUser.get(). This simulates the normal cache path.
        cache_key = "freeipa_user_bob"
        full_data: dict[str, object] = {
            "uid": ["bob"],
            "givenname": ["Bob"],
            "sn": ["User"],
            "mail": ["bob@example.org"],
            "fasIsPrivate": ["TRUE"],
        }
        cache.set(cache_key, full_data)

        try:
            # Viewer is someone else => anonymized.
            set_current_viewer_username("alice")
            try:
                bob_for_alice = FreeIPAUser.get("bob")
                self.assertIsNotNone(bob_for_alice)
                assert bob_for_alice is not None
                self.assertEqual(bob_for_alice.full_name, "bob")
            finally:
                clear_current_viewer_username()

            # Viewer is self => should still see the full name from cached data.
            set_current_viewer_username("bob")
            try:
                bob_for_bob = FreeIPAUser.get("bob")
                self.assertIsNotNone(bob_for_bob)
                assert bob_for_bob is not None
                self.assertEqual(bob_for_bob.full_name, "Bob User")
            finally:
                clear_current_viewer_username()
        finally:
            cache.delete(cache_key)

    def test_user_profile_anonymizes_private_user_for_non_self_viewer(self) -> None:
        factory = RequestFactory()
        request = factory.get("/user/bob/")
        request.user = SimpleNamespace(is_authenticated=True, get_username=lambda: "alice")

        set_current_viewer_username("alice")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasPronoun": ["they/them"],
                    "fasWebsiteUrl": ["https://example.invalid"],
                    "fasIsPrivate": ["TRUE"],
                    # Keep group data present to ensure it is not redacted.
                    "memberof_group": ["packagers"],
                },
            )
        finally:
            clear_current_viewer_username()

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
        ):
            resp = views_users.user_profile(request, "bob")

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Private data is redacted
        self.assertNotIn("Bob User", html)
        self.assertNotIn("they/them", html)
        self.assertNotIn("example.invalid", html)
        self.assertNotIn("bob@example.org", html)

        # Username remains visible
        self.assertIn("bob", html)
