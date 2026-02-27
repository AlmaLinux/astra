
from unittest.mock import patch

from django.test import TestCase

from core.freeipa.user import FreeIPAUser


class FreeIPAUserSaveTrimsCnTests(TestCase):
    def test_save_strips_cn_components(self) -> None:
        user = FreeIPAUser("sej7278", {"uid": ["sej7278"]})
        user.first_name = "Sej"
        user.last_name = ""
        user.email = "sej7278@example.org"

        captured: dict[str, object] = {}

        class _FakeClient:
            def user_mod(self, _username: str, **updates: object) -> dict[str, object]:
                captured.update(updates)
                return {"result": {"uid": ["sej7278"]}}

        def _fake_retry(_get_client, fn):
            return fn(_FakeClient())

        with patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=_fake_retry):
            user.save()

        self.assertEqual(captured.get("o_cn"), "Sej")
        self.assertEqual(captured.get("o_displayname"), "Sej")
        self.assertEqual(captured.get("o_gecos"), "Sej")
        self.assertEqual(captured.get("o_initials"), "S")
