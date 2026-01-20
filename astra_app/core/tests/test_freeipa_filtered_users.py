from __future__ import annotations

from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from core.backends import FreeIPAUser


class FreeIPAFilteredUsersTests(TestCase):
    def setUp(self) -> None:
        cache.delete("freeipa_users_all")

    @override_settings(
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_FILTERED_USERNAMES=frozenset({"admin", "svc"}),
    )
    def test_all_excludes_filtered_usernames(self) -> None:
        client = Mock()
        client.user_find.return_value = {
            "result": [
                {"uid": ["admin"]},
                {"uid": ["svc"]},
                {"uid": ["alice"]},
            ]
        }

        with patch("core.backends.FreeIPAUser.get_client", autospec=True, return_value=client):
            users = FreeIPAUser.all()

        self.assertEqual([u.username for u in users], ["alice"])

    @override_settings(
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_FILTERED_USERNAMES=frozenset({"admin", "svc"}),
    )
    def test_get_still_returns_filtered_user(self) -> None:
        client = Mock()
        client.user_show.return_value = {"result": {"uid": ["admin"], "mail": ["admin@example.com"]}}

        with patch("core.backends.FreeIPAUser.get_client", autospec=True, return_value=client):
            user = FreeIPAUser.get("admin")

        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.username, "admin")
