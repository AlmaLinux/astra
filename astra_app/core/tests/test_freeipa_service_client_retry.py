from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase
from python_freeipa import exceptions

from core.freeipa.user import FreeIPAUser


def _clear_service_client_cache() -> None:
    # Import lazily so the tests remain robust if internals move.
    from core.freeipa.client import clear_freeipa_service_client_cache

    clear_freeipa_service_client_cache()


class FreeIPAServiceClientRetryTests(TestCase):
    def setUp(self):
        cache.delete("freeipa_users_all")
        cache.delete("freeipa_user_alice")
        _clear_service_client_cache()

    def test_all_retries_on_unauthorized(self):
        first_client = Mock()
        second_client = Mock()

        first_client.user_find.side_effect = exceptions.Unauthorized()
        second_client.user_find.return_value = {"result": [{"uid": ["alice"]}]}

        with patch(
            "core.freeipa.user.FreeIPAUser.get_client",
            autospec=True,
            side_effect=[first_client, second_client],
        ) as get_client:
            users = FreeIPAUser.all()

        self.assertEqual([u.username for u in users], ["alice"])
        self.assertEqual(get_client.call_count, 2)

    def test_get_retries_on_unauthorized(self):
        first_client = Mock()
        second_client = Mock()

        first_client.user_show.side_effect = exceptions.Unauthorized()
        second_client.user_show.return_value = {"result": {"uid": ["alice"], "mail": ["a@example.com"]}}

        with patch(
            "core.freeipa.user.FreeIPAUser.get_client",
            autospec=True,
            side_effect=[first_client, second_client],
        ) as get_client:
            user = FreeIPAUser.get("alice")

        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.username, "alice")
        self.assertEqual(get_client.call_count, 2)

    def test_get_raises_on_password_expired(self) -> None:
        with patch(
            "core.freeipa.client._get_freeipa_client",
            autospec=True,
            side_effect=exceptions.PasswordExpired(),
        ):
            with self.assertLogs("core.backends", level="ERROR") as captured:
                with self.assertRaises(exceptions.PasswordExpired):
                    FreeIPAUser.get("alice")

        # Ensure we emit a clear log line (not just a silent None).
        combined = "\n".join(captured.output).lower()
        self.assertIn("password expired", combined)
