
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAUser


class FreeIPAUserInitialsSyncTests(TestCase):
    def test_create_sets_initials(self) -> None:
        calls: list[dict[str, object]] = []

        class DummyClient:
            def user_add(self, username, givenname, sn, cn, **kwargs):
                calls.append({"username": username, "givenname": givenname, "sn": sn, "cn": cn, "kwargs": kwargs})
                return {"result": {"uid": [username]}}

        def fake_retry(_get_client, fn):
            return fn(DummyClient())

        with (
            patch("core.backends._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.backends.FreeIPAUser.get", return_value=None),
            patch("core.backends._invalidate_users_list_cache"),
        ):
            FreeIPAUser.create("alice", first_name="Alice", last_name="User", email="a@example.com")

        self.assertTrue(calls)
        kwargs = calls[0]["kwargs"]
        self.assertEqual(kwargs.get("o_initials"), "AU")

    def test_save_updates_initials(self) -> None:
        calls: list[dict[str, object]] = []

        class DummyClient:
            def user_mod(self, username, **kwargs):
                calls.append({"username": username, "kwargs": kwargs})
                return {"result": {"uid": [username]}}

        def fake_retry(_get_client, fn):
            return fn(DummyClient())

        user = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "cn": ["Alice User"], "mail": [""]},
        )
        user.first_name = "Ada"
        user.last_name = "Lovelace"

        with (
            patch("core.backends._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.backends._invalidate_user_cache"),
            patch("core.backends._invalidate_users_list_cache"),
            patch("core.backends.FreeIPAUser.get", return_value=user),
        ):
            user.save()

        self.assertTrue(calls)
        self.assertEqual(calls[0]["kwargs"].get("o_initials"), "AL")
