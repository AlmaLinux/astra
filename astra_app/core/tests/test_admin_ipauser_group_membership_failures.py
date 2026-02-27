
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import IPAUser


class AdminIPAUserGroupMembershipFailureTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        # These unmanaged models use app_label="auth"; create ContentType rows
        # so successful admin saves can write LogEntry without deferred FK errors.
        ContentType.objects.get_for_model(IPAUser)

    def _login_as_freeipa_admin(self, username: str = "alex") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_user_add_group_failure_shows_error_and_stays(self) -> None:
        username = "alex"
        # The object being edited (also alex for simplicity).
        target_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["User"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        self._login_as_freeipa_admin(username)

        def fake_retry(_get_client, fn):
            class DummyClient:
                def user_mod(self, _username: str, **_updates: object):
                    return {"result": {}}

                def group_add_member(self, group: str, **kwargs: object):
                    # Simulate FreeIPA returning a partial failure without raising.
                    if group == "ambassadors":
                        return {
                            "result": {"completed": 0},
                            "failed": {"member": {"user": {username: "not allowed"}}},
                        }
                    return {"result": {"completed": 1}}

            return fn(DummyClient())

        def fake_user_get(u: str):
            # The admin auth middleware loads the session user; the change view
            # then loads the edited user. Keep it simple: both are alex here.
            if u == username:
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=None),
            patch("core.admin.FreeIPAGroup.all", return_value=[SimpleNamespace(cn="admins"), SimpleNamespace(cn="ambassadors")]),
        ):
            url = reverse("admin:auth_ipauser_change", args=[username])
            resp = self.client.post(
                url,
                data={
                    "username": username,
                    "first_name": "Alex",
                    "last_name": "User",
                    "email": "alex@example.org",
                    "is_active": "on",
                    "groups": ["admins", "ambassadors"],
                    "_save": "Save",
                },
                follow=False,
            )

        # We should remain on the change page (not redirect to changelist).
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/auth/ipauser/alex/change/", resp["Location"])

        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("ambassadors" in m.lower() for m in messages))

    def test_admin_user_remove_group_failure_has_details(self) -> None:
        """If FreeIPA returns a structured failure with empty leaf values, still show details."""

        username = "alex"

        target_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["User"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins", "board"],
            },
        )

        self._login_as_freeipa_admin(username)

        def fake_retry(_get_client, fn):
            class DummyClient:
                def user_mod(self, _username: str, **_updates: object):
                    return {"result": {}}

                def group_remove_member(self, group: str, **_kwargs: object):
                    if group == "board":
                        # Truthy dict, but all leaves are empty (no concrete string error).
                        return {"result": {"completed": 0}, "failed": {"member": {"user": {username: []}}}}
                    return {"result": {"completed": 1}}

            return fn(DummyClient())

        def fake_user_get(u: str):
            if u == username:
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=None),
            patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[SimpleNamespace(cn="admins"), SimpleNamespace(cn="board")],
            ),
        ):
            url = reverse("admin:auth_ipauser_change", args=[username])
            resp = self.client.post(
                url,
                data={
                    "username": username,
                    "first_name": "Alex",
                    "last_name": "User",
                    "email": "alex@example.org",
                    "is_active": "on",
                    # Remove board.
                    "groups": ["admins"],
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/auth/ipauser/alex/change/", resp["Location"])

        msgs = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("board" in m.lower() for m in msgs))
        # Regression: previously this ended with a trailing colon and no details.
        self.assertTrue(any("failed=" in m.lower() for m in msgs), msgs)

    def test_admin_user_add_group_success_but_not_applied_shows_error(self) -> None:
        """If FreeIPA reports success but the user is still not a member after refresh, fail."""

        username = "alex"
        self._login_as_freeipa_admin(username)

        target_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["User"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        def fake_retry(_get_client, fn):
            class DummyClient:
                def user_mod(self, _username: str, **_updates: object):
                    return {"result": {}}

                def group_add_member(self, group: str, **_kwargs: object):
                    # Looks successful but we will keep returning stale user data.
                    return {"result": {"completed": 1}}

            return fn(DummyClient())

        def fake_user_get(u: str):
            if u == username:
                return target_user
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=None),
            patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[SimpleNamespace(cn="admins"), SimpleNamespace(cn="ambassadors")],
            ),
        ):
            url = reverse("admin:auth_ipauser_change", args=[username])
            resp = self.client.post(
                url,
                data={
                    "username": username,
                    "first_name": "Alex",
                    "last_name": "User",
                    "email": "alex@example.org",
                    "is_active": "on",
                    "groups": ["admins", "ambassadors"],
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/auth/ipauser/alex/change/", resp["Location"])

        msgs = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("ambassadors" in m.lower() for m in msgs), msgs)
        self.assertTrue(any("not present" in m.lower() for m in msgs), msgs)

    def test_admin_user_remove_group_empty_failed_skeleton_is_not_error(self) -> None:
        """FreeIPA may return a `failed` skeleton with empty lists on success."""

        username = "alex"
        self._login_as_freeipa_admin(username)

        # Before: user is in general.
        before = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["User"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins", "general"],
            },
        )
        # After: membership is removed.
        after = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alex"],
                "sn": ["User"],
                "mail": ["alex@example.org"],
                "memberof_group": ["admins"],
            },
        )

        state: dict[str, object] = {"removed": False}

        def fake_retry(_get_client, fn):
            class DummyClient:
                def user_mod(self, _username: str, **_updates: object):
                    return {"result": {}}

                def group_remove_member(self, group: str, **_kwargs: object):
                    if group == "general":
                        state["removed"] = True
                        return {
                            "result": {"completed": 1},
                            "failed": {
                                "member": {
                                    "user": [],
                                    "group": [],
                                    "service": [],
                                    "idoverrideuser": [],
                                }
                            },
                        }
                    return {"result": {"completed": 1}}

            return fn(DummyClient())

        def fake_user_get(u: str):
            if u != username:
                return None
            return after if state["removed"] else before

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_user_get),
            patch("core.freeipa.user._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=None),
            patch("core.admin.FreeIPAUser.all", return_value=[after]),
            patch("core.admin.IPAUserAdmin.log_change"),
            patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[SimpleNamespace(cn="admins"), SimpleNamespace(cn="general")],
            ),
        ):
            url = reverse("admin:auth_ipauser_change", args=[username])
            resp = self.client.post(
                url,
                data={
                    "username": username,
                    "first_name": "Alex",
                    "last_name": "User",
                    "email": "alex@example.org",
                    "is_active": "on",
                    # Remove general.
                    "groups": ["admins"],
                    "_save": "Save",
                },
                follow=False,
            )

        # Success path should redirect to changelist.
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/auth/ipauser/", resp["Location"])

        msgs = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertFalse(any("freeipa group_remove_member failed" in m.lower() for m in msgs), msgs)
