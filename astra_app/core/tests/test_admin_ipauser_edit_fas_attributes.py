from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import IPAUser


class AdminIPAUserEditFASAttributesTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        # These unmanaged models use app_label="auth"; create ContentType rows
        # so successful admin saves can write LogEntry without deferred FK errors.
        ContentType.objects.get_for_model(IPAUser)

    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_user_change_updates_fas_attributes(self) -> None:
        admin_username = "alex"
        target_username = "alice"

        freeipa_admin = FreeIPAUser(
            admin_username,
            {
                "uid": [admin_username],
                "givenname": ["Alex"],
                "sn": ["Admin"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        target_user = FreeIPAUser(
            target_username,
            {
                "uid": [target_username],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
                "fasGitHubUsername": ["oldhandle"],
                "fasIsPrivate": ["FALSE"],
                "fasPronoun": ["they/them"],
            },
        )

        self._login_as_freeipa_admin(admin_username)

        class DummyClient:
            def __init__(self) -> None:
                self.user_mod_calls: list[tuple[str, dict[str, object]]] = []

            def user_find(self, **_kwargs: object) -> dict[str, object]:
                # The admin changelist/object lookup uses user_find; return our
                # target user so the change view can resolve it.
                return {"result": [dict(target_user._user_data)]}

            def user_mod(self, username: str, **updates: object) -> dict[str, object]:
                self.user_mod_calls.append((username, dict(updates)))
                return {"result": {}}

        dummy_client = DummyClient()

        def fake_retry(_get_client, fn):
            return fn(dummy_client)

        def fake_user_get(username: str):
            if username == admin_username:
                return freeipa_admin
            if username == target_username:
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=dummy_client),
            patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.admin.FreeIPAGroup.all", return_value=[SimpleNamespace(cn="admins")]),
        ):
            url = reverse("admin:auth_ipauser_change", args=[target_username])
            resp = self.client.post(
                url,
                data={
                    "username": target_username,
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.org",
                    "is_active": "on",
                    "groups": [],
                    # New desired FAS values.
                    "fasGitHubUsername": "newhandle",
                    "fasIsPrivate": "on",
                    "fasPronoun": "she/her, they/them",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)

        fas_calls = [c for c in dummy_client.user_mod_calls if "o_setattr" in c[1] or "o_addattr" in c[1]]
        self.assertTrue(fas_calls, dummy_client.user_mod_calls)

        combined_updates: dict[str, object] = {}
        for _username, updates in fas_calls:
            combined_updates.update(updates)

        setattr_values = combined_updates.get("o_setattr", [])
        addattr_values = combined_updates.get("o_addattr", [])

        # GitHub handle and privacy flag should be updated via setattr.
        self.assertIn("fasGitHubUsername=newhandle", list(setattr_values))
        self.assertIn("fasIsPrivate=TRUE", list(setattr_values))

        # Pronouns should be added as a multivalued attribute.
        self.assertIn("fasPronoun=she/her", list(addattr_values))

    def test_admin_user_change_rejects_invalid_github_username(self) -> None:
        admin_username = "alex"
        target_username = "alice"

        freeipa_admin = FreeIPAUser(
            admin_username,
            {
                "uid": [admin_username],
                "givenname": ["Alex"],
                "sn": ["Admin"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        target_user = FreeIPAUser(
            target_username,
            {
                "uid": [target_username],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_admin(admin_username)

        class DummyClient:
            def __init__(self) -> None:
                self.user_mod_calls: list[tuple[str, dict[str, object]]] = []

            def user_find(self, **_kwargs: object) -> dict[str, object]:
                return {"result": [dict(target_user._user_data)]}

            def user_mod(self, username: str, **updates: object) -> dict[str, object]:
                self.user_mod_calls.append((username, dict(updates)))
                return {"result": {}}

        dummy_client = DummyClient()

        def fake_user_get(username: str):
            if username == admin_username:
                return freeipa_admin
            if username == target_username:
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=dummy_client),
            patch("core.admin.FreeIPAGroup.all", return_value=[SimpleNamespace(cn="admins")]),
        ):
            url = reverse("admin:auth_ipauser_change", args=[target_username])
            resp = self.client.post(
                url,
                data={
                    "username": target_username,
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.org",
                    "is_active": "on",
                    "groups": [],
                    "fasGitHubUsername": "bad handle with spaces",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        form = resp.context["adminform"].form
        self.assertIn("fasGitHubUsername", form.errors)
        self.assertTrue(any("not valid" in str(e).lower() for e in form.errors["fasGitHubUsername"]))
        self.assertEqual(dummy_client.user_mod_calls, [])

    def test_admin_user_change_rejects_invalid_timezone(self) -> None:
        admin_username = "alex"
        target_username = "alice"

        freeipa_admin = FreeIPAUser(
            admin_username,
            {
                "uid": [admin_username],
                "givenname": ["Alex"],
                "sn": ["Admin"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        target_user = FreeIPAUser(
            target_username,
            {
                "uid": [target_username],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.org"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_admin(admin_username)

        class DummyClient:
            def __init__(self) -> None:
                self.user_mod_calls: list[tuple[str, dict[str, object]]] = []

            def user_find(self, **_kwargs: object) -> dict[str, object]:
                return {"result": [dict(target_user._user_data)]}

            def user_mod(self, username: str, **updates: object) -> dict[str, object]:
                self.user_mod_calls.append((username, dict(updates)))
                return {"result": {}}

        dummy_client = DummyClient()

        def fake_user_get(username: str):
            if username == admin_username:
                return freeipa_admin
            if username == target_username:
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user.FreeIPAUser.get_client", return_value=dummy_client),
            patch("core.admin.FreeIPAGroup.all", return_value=[SimpleNamespace(cn="admins")]),
        ):
            url = reverse("admin:auth_ipauser_change", args=[target_username])
            resp = self.client.post(
                url,
                data={
                    "username": target_username,
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.org",
                    "is_active": "on",
                    "groups": [],
                    "fasTimezone": "Not/AZone",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        form = resp.context["adminform"].form
        self.assertIn("fasTimezone", form.errors)
        self.assertTrue(any("iana" in str(e).lower() for e in form.errors["fasTimezone"]))
        self.assertEqual(dummy_client.user_mod_calls, [])
