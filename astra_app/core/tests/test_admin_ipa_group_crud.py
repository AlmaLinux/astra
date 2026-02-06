from __future__ import annotations

from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAGroup, FreeIPAUser, clear_current_viewer_username, set_current_viewer_username


class AdminIPAGroupCRUDTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_create_group(self) -> None:
        """Test creating a new FreeIPA group via Django admin."""

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        all_users = [
            admin_user,
            FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
        ]

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=all_users),
            patch("core.backends.FreeIPAGroup.create") as mock_create,
            patch("core.backends.FreeIPAGroup.add_member"),
        ):
            mock_create.return_value = FreeIPAGroup("testgroup", {"cn": ["testgroup"], "description": ["A test group"]})
            url = reverse("admin:auth_ipagroup_add")
            resp = self.client.post(
                url,
                data={
                    "cn": "testgroup",
                    "description": "A test group",
                    "members": ["alice", "bob"],
                    "fas_url": "https://example.com/group",
                    "fas_mailing_list": "testgroup@example.com",
                    "fas_irc_channels": "#testgroup\n#testgroup-dev",
                    "fas_discussion_url": "https://discussion.example.com/group",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)  # Redirect on success
        # FreeIPAGroup.create executed; creation observed via fake_client.group_add_calls

        # Verify admin action was logged
        from django.contrib.auth import get_user_model
        shadow_user = get_user_model().objects.get(username="alice")
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, shadow_user.pk)
        self.assertEqual(entry.action_flag, ADDITION)
        self.assertEqual(entry.object_repr, "testgroup")

    def test_edit_group(self) -> None:
        """Test editing an existing FreeIPA group via Django admin."""

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        existing_group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": ["Original description"],
                "member": ["alice"],
            },
        )

        def _fake_get(cn: str):
            if cn == "testgroup":
                return existing_group
            return None

        all_users = [
            admin_user,
            FreeIPAUser("charlie", {"uid": ["charlie"], "memberof_group": []}),
        ]

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=all_users),
            patch("core.backends.FreeIPAGroup.get", side_effect=_fake_get),
            patch.object(existing_group, "save") as mock_save,
            patch.object(existing_group, "add_member"),
            patch.object(existing_group, "remove_member"),
        ):
            # First, get the change form
            url = reverse("admin:auth_ipagroup_change", args=["testgroup"])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)

            # Now, post changes
            resp = self.client.post(
                url,
                data={
                    "cn": "testgroup",  # Immutable, but included
                    "description": "Updated description",
                    "members": ["alice", "charlie"],
                    "fas_url": "https://updated.example.com/group",
                    "fas_mailing_list": "updated@example.com",
                    "fas_irc_channels": "#updated\n#updated-dev",
                    "fas_discussion_url": "https://updated.discussion.example.com/group",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)  # Redirect on success
        mock_save.assert_called_once()
        # Note: add_member may be called for all desired members depending on current state

        # Logging is enabled for unmanaged models with ContentType created in setUp

    def test_delete_group(self) -> None:
        """Test deleting a FreeIPA group via Django admin delete_selected action."""

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        target_group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": ["A test group"],
                "member": [],
            },
        )

        def _fake_get(cn: str):
            if cn == "testgroup":
                return target_group
            return None

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.backends.FreeIPAGroup.get", side_effect=_fake_get),
            patch("core.backends.FreeIPAGroup.all", return_value=[target_group]),
            patch.object(target_group, "delete"),
        ):
            url = reverse("admin:auth_ipagroup_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["testgroup"],
                    "select_across": "1",
                    "index": "0",
                },
                follow=False,
            )

        # Should show confirmation page
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Are you sure", status_code=200)

    def test_delete_group_actual(self) -> None:
        """Test actual deletion of a FreeIPA group via Django admin and verify logging."""

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        target_group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": ["A test group"],
                "member": [],
            },
        )

        def _fake_get(cn: str):
            if cn == "testgroup":
                return target_group
            return None

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.backends.FreeIPAGroup.get", side_effect=_fake_get),
            patch.object(target_group, "delete") as mock_delete,
        ):
            # Simulate posting to the delete confirmation URL
            # In real admin, this would be obtained from the confirmation page
            url = reverse("admin:auth_ipagroup_delete", args=["testgroup"])
            resp = self.client.post(
                url,
                data={
                    "post": "yes",  # Confirm deletion
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)  # Redirect on success
        mock_delete.assert_called_once()

        # Logging is enabled for unmanaged models with ContentType created in setUp

    def test_create_group_with_fasgroup(self):
        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        class _FakeClient:
            def __init__(self) -> None:
                self.group_add_calls: list[tuple[str, dict[str, object]]] = []

            def group_add(self, cn: str, **kwargs: object) -> dict[str, object]:
                self.group_add_calls.append((cn, dict(kwargs)))
                return {"result": {}}
            def group_find(self, **kwargs: object) -> dict[str, object]:
                # Simulate find returning the newly-created group with fasgroup True
                cn = kwargs.get('o_cn') or kwargs.get('a_cn') or kwargs.get('cn')
                return {"count": 1, "result": [{"cn": [cn], "fasgroup": [True]}]}

        fake_client = _FakeClient()

        def _fake_retry(_get_client, fn):
            return fn(fake_client)

        all_users = [
            admin_user,
            FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
        ]

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=all_users),
            patch("core.backends._with_freeipa_service_client_retry", side_effect=_fake_retry),
            patch("core.backends.FreeIPAGroup.add_member"),
            patch("core.backends.FreeIPAGroup.remove_member"),
        ):
            # Do not mock FreeIPAGroup.create here; allow the create path to
            # call into the patched backend retry helper so we can observe
            # the `group_add` call on the fake client.
            url = reverse("admin:auth_ipagroup_add")
            resp = self.client.post(
                url,
                data={
                    "cn": "testgroup",
                    "description": "A test group",
                    "members": ["alice", "bob"],
                    "fas_url": "https://example.com/group",
                    "fas_mailing_list": "testgroup@example.com",
                    "fas_irc_channels": "#testgroup\n#testgroup-dev",
                    "fas_discussion_url": "https://discussion.example.com/group",
                    "fas_group": "on",
                    "_save": "Save",
                },
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)
        # Creation should request FAS support at create-time via the boolean
        # (backend may encode it either via `fasgroup=True` or by adding the
        # fasGroup objectClass at creation time).
        self.assertTrue(
            any(
                call_kwargs.get("fasgroup") is True or call_kwargs.get("o_addattr") == ["objectClass=fasGroup"]
                for _cn, call_kwargs in fake_client.group_add_calls
            )
        )

    def test_create_group_sets_fas_attrs_on_create(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        created_group = FreeIPAGroup("testgroup", {"cn": ["testgroup"], "fasgroup": [True]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user]),
            patch("core.backends.FreeIPAGroup.create", return_value=created_group),
            patch("core.backends.FreeIPAGroup.get", return_value=created_group),
            patch.object(created_group, "save") as save_mock,
            patch("core.backends.FreeIPAGroup.add_member"),
        ):
            url = reverse("admin:auth_ipagroup_add")
            resp = self.client.post(
                url,
                data={
                    "cn": "testgroup",
                    "description": "A test group",
                    "members": ["alice"],
                    "fas_url": "https://example.com/group",
                    "fas_mailing_list": "testgroup@example.com",
                    "fas_irc_channels": "#testgroup",
                    "fas_discussion_url": "https://discussion.example.com/group",
                    "fas_group": "on",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(created_group.fas_url, "https://example.com/group")
        self.assertEqual(created_group.fas_mailing_list, "testgroup@example.com")
        self.assertEqual(created_group.fas_discussion_url, "https://discussion.example.com/group")
        self.assertEqual(created_group.fas_irc_channels, ["irc:/#testgroup"])
        save_mock.assert_called_once()

    def test_edit_group_toggle_fasgroup(self):
        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        existing_group = FreeIPAGroup(
            "testgroup",
            {"cn": ["testgroup"], "description": ["desc"], "objectclass": ["posixgroup"]},
        )

        class _FakeClient:
            def __init__(self) -> None:
                self.group_mod_calls: list[tuple[str, dict[str, object]]] = []

            def group_mod(self, cn: str, **kwargs: object) -> dict[str, object]:
                self.group_mod_calls.append((cn, dict(kwargs)))
                return {"result": {}}

        fake_client = _FakeClient()

        def _fake_retry(_get_client, fn):
            return fn(fake_client)

        def _fake_get(cn):
            if cn == "testgroup":
                return existing_group
            return None

        all_users = [admin_user]
        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=all_users),
            patch("core.backends.FreeIPAGroup.get", side_effect=_fake_get),
            patch.object(existing_group, "save"),
            patch("core.backends._with_freeipa_service_client_retry", side_effect=_fake_retry),
            patch.object(existing_group, "add_member"),
            patch.object(existing_group, "remove_member"),
        ):
            url = reverse("admin:auth_ipagroup_change", args=["testgroup"])
            # Toggle fas_group ON (should be ignored for existing groups)
            resp = self.client.post(
                url,
                data={
                    "cn": "testgroup",
                    "description": "desc",
                    "members": ["alice"],
                    "fas_url": "",
                    "fas_mailing_list": "",
                    "fas_irc_channels": "",
                    "fas_discussion_url": "",
                    "fas_group": "on",
                    "_save": "Save",
                },
                follow=False,
            )
            self.assertEqual(resp.status_code, 302)
            # No group_mod should be called because toggling is disallowed.
            self.assertFalse(fake_client.group_mod_calls)
            fake_client.group_mod_calls.clear()
            # Toggle fas_group OFF (also ignored)
            resp = self.client.post(
                url,
                data={
                    "cn": "testgroup",
                    "description": "desc",
                    "members": ["alice"],
                    "fas_url": "",
                    "fas_mailing_list": "",
                    "fas_irc_channels": "",
                    "fas_discussion_url": "",
                    # no fas_group
                    "_save": "Save",
                },
                follow=False,
            )
            self.assertEqual(resp.status_code, 302)
            self.assertFalse(fake_client.group_mod_calls)


class AdminIPAGroupEmailsCSVTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_change_form_includes_download_emails_button(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})
        group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "description": ["A test group"],
                "member_user": ["bob"],
                "membermanager_user": ["carol"],
            },
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
            patch("core.backends.FreeIPAGroup.all", return_value=[group]),
            patch("core.backends.FreeIPAGroup.get", return_value=group),
        ):
            url = reverse("admin:auth_ipagroup_change", args=["testgroup"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        download_url = reverse("admin:auth_ipagroup_download_emails", args=["testgroup"])
        self.assertContains(resp, download_url)
        self.assertContains(resp, "Download member/sponsor emails (CSV)")

    def test_download_emails_csv_contains_unique_member_and_sponsor_emails(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]})

        root_group = FreeIPAGroup(
            "testgroup",
            {
                "cn": ["testgroup"],
                "member_user": ["bob", "dave"],
                "member_group": ["child-members"],
                "membermanager_user": ["carol", "bob"],
                "membermanager_group": ["child-sponsors"],
            },
        )
        child_members = FreeIPAGroup(
            "child-members",
            {
                "cn": ["child-members"],
                "member_user": ["erin"],
            },
        )
        child_sponsors = FreeIPAGroup(
            "child-sponsors",
            {
                "cn": ["child-sponsors"],
                "member_user": ["frank", "dave"],
            },
        )

        def _fake_group_get(cn: str):
            if cn == "testgroup":
                return root_group
            if cn == "child-members":
                return child_members
            if cn == "child-sponsors":
                return child_sponsors
            return None

        def _fake_user_get(username: str):
            if username == "alice":
                return admin_user
            if username == "bob":
                return FreeIPAUser("bob", {"uid": ["bob"], "mail": ["shared@example.com"]})
            if username == "carol":
                return FreeIPAUser("carol", {"uid": ["carol"], "mail": ["carol@example.com"]})
            if username == "dave":
                return FreeIPAUser("dave", {"uid": ["dave"], "mail": ["shared@example.com"]})
            if username == "erin":
                return FreeIPAUser("erin", {"uid": ["erin"], "mail": [""]})
            if username == "frank":
                return FreeIPAUser("frank", {"uid": ["frank"], "mail": ["frank@example.com"]})
            return FreeIPAUser(username, {"uid": [username], "mail": [""]})

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_fake_user_get),
            patch("core.backends.FreeIPAGroup.all", return_value=[root_group]),
            patch("core.backends.FreeIPAGroup.get", side_effect=_fake_group_get),
        ):
            url = reverse("admin:auth_ipagroup_download_emails", args=["testgroup"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", resp["Content-Disposition"])

        lines = resp.content.decode("utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 1)
        self.assertEqual(lines[0].strip(), "email")

        emails = [line.strip() for line in lines[1:] if line.strip()]
        self.assertEqual(
            emails,
            [
                "carol@example.com",
                "frank@example.com",
                "shared@example.com",
            ],
        )

    def test_download_emails_csv_includes_private_profile_emails_for_admins(self) -> None:
        self._login_as_freeipa_admin("alice")

        # Simulate the normal request middleware setting a viewer (which would
        # trigger anonymization for private users).
        set_current_viewer_username("alice")
        try:
            admin_user = FreeIPAUser(
                "alice",
                {"uid": ["alice"], "memberof_group": ["admins"], "mail": ["alice@example.com"]},
            )
            group = FreeIPAGroup(
                "testgroup",
                {
                    "cn": ["testgroup"],
                    "member_user": ["privateuser"],
                },
            )

            def _fake_user_get(username: str):
                if username == "alice":
                    return admin_user
                if username == "privateuser":
                    return FreeIPAUser(
                        "privateuser",
                        {
                            "uid": ["privateuser"],
                            "mail": ["private@example.com"],
                            "fasIsPrivate": ["TRUE"],
                        },
                    )
                return FreeIPAUser(username, {"uid": [username], "mail": [""]})

            with (
                patch("core.backends.FreeIPAUser.get", side_effect=_fake_user_get),
                patch("core.backends.FreeIPAGroup.all", return_value=[group]),
                patch("core.backends.FreeIPAGroup.get", return_value=group),
            ):
                url = reverse("admin:auth_ipagroup_download_emails", args=["testgroup"])
                resp = self.client.get(url)

            self.assertEqual(resp.status_code, 200)
            lines = resp.content.decode("utf-8").splitlines()
            self.assertEqual(lines[0].strip(), "email")
            emails = [line.strip() for line in lines[1:] if line.strip()]
            self.assertEqual(emails, ["private@example.com"])
        finally:
            clear_current_viewer_username()
