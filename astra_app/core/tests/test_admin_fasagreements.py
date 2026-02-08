
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.urls import reverse
from python_freeipa.exceptions import Denied

from core.admin import IPAFASAgreementAdmin
from core.backends import FreeIPAUser
from core.models import IPAFASAgreement


class AdminFASAgreementTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_changelist_renders_agreements(self):
        username = "alice"
        freeipa_user = FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})
        self._login_as_freeipa_admin(username)

        agreement = MagicMock()
        agreement.cn = "fpca"
        agreement.description = "Fedora Project Contributor Agreement"
        agreement.enabled = True

        with patch("core.backends.FreeIPAUser.get", return_value=freeipa_user):
            with patch("core.admin.FreeIPAFASAgreement.all", return_value=[agreement]):
                url = reverse("admin:auth_ipafasagreement_changelist")
                resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "fpca")

    def test_admin_can_add_agreement(self):
        username = "alice"
        freeipa_user = FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})
        self._login_as_freeipa_admin(username)

        group = MagicMock()
        group.cn = "myfasgroup"
        user = MagicMock()
        user.username = "bob"

        created = MagicMock()
        created.cn = "fpca"
        created.description = "desc"
        created.enabled = True

        with (
            patch("core.backends.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.admin.FreeIPAGroup.all", return_value=[group]),
            patch("core.admin.FreeIPAUser.all", return_value=[user]),
            patch("core.admin.FreeIPAFASAgreement.create", return_value=created) as create,
        ):
            url = reverse("admin:auth_ipafasagreement_add")
            resp = self.client.post(
                url,
                data={
                    "cn": "fpca",
                    "description": "desc",
                    "enabled": "on",
                    "groups": ["myfasgroup"],
                    "users": ["bob"],
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        create.assert_called_once()
        created.add_group.assert_called_once_with("myfasgroup")
        created.add_user.assert_called_once_with("bob")

    def test_disabling_agreement_unlinks_groups(self):
        freeipa = MagicMock()
        freeipa.cn = "fpca"
        freeipa.description = "desc"
        freeipa.enabled = True
        freeipa.groups = ["g1", "g2"]
        freeipa.users = ["bob"]

        admin_obj = IPAFASAgreementAdmin(IPAFASAgreement, AdminSite())

        form = MagicMock()
        form.cleaned_data = {
            "cn": "fpca",
            "description": "desc",
            "enabled": False,
            "groups": ["myfasgroup"],
            "users": ["bob"],
        }

        obj = IPAFASAgreement(cn="fpca")

        with patch("core.admin.FreeIPAFASAgreement.get", return_value=freeipa):
            admin_obj.save_model(request=MagicMock(), obj=obj, form=form, change=True)

        freeipa.remove_group.assert_any_call("g1")
        freeipa.remove_group.assert_any_call("g2")
        freeipa.set_enabled.assert_called_once_with(False)

    def test_bulk_delete_denied_shows_error_message(self):
        username = "alice"
        freeipa_user = FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})
        self._login_as_freeipa_admin(username)

        listed = MagicMock()
        listed.cn = "test_agreement"
        listed.description = ""
        listed.enabled = True

        freeipa = MagicMock()
        freeipa.delete.side_effect = Denied(
            "Insufficient access: Not allowed to delete User Agreement with linked groups",
            0,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.admin.FreeIPAFASAgreement.all", return_value=[listed]),
            patch("core.admin.FreeIPAFASAgreement.get", return_value=freeipa),
        ):
            url = reverse("admin:auth_ipafasagreement_changelist")
            confirm = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["test_agreement"],
                },
                follow=False,
            )

            self.assertEqual(confirm.status_code, 200)
            self.assertContains(confirm, "Are you sure", status_code=200)

            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["test_agreement"],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Failed to delete")
        self.assertContains(resp, "Not allowed to delete User Agreement with linked groups")
        self.assertNotContains(resp, "Successfully deleted")

    def test_delete_view_denied_shows_error_message(self):
        username = "alice"
        freeipa_user = FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})
        self._login_as_freeipa_admin(username)

        freeipa = MagicMock()
        freeipa.delete.side_effect = Denied(
            "Insufficient access: Not allowed to delete User Agreement with linked groups",
            0,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.admin.FreeIPAFASAgreement.all", return_value=[MagicMock(cn="test_agreement", description="", enabled=True)]),
            patch("core.admin.FreeIPAFASAgreement.get", return_value=freeipa),
        ):
            url = reverse("admin:auth_ipafasagreement_delete", args=["test_agreement"])
            resp = self.client.post(url, data={"post": "yes"}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Not allowed to delete User Agreement with linked groups")

    def test_bulk_delete_success_shows_success_only(self):
        username = "alice"
        freeipa_user = FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})
        self._login_as_freeipa_admin(username)

        listed1 = MagicMock(cn="test1", description="", enabled=True)
        listed2 = MagicMock(cn="test2", description="", enabled=True)
        listed3 = MagicMock(cn="test3", description="", enabled=True)

        f1 = MagicMock()
        f1.delete.return_value = None
        f2 = MagicMock()
        f2.delete.return_value = None
        f3 = MagicMock()
        f3.delete.return_value = None

        def _fake_get(cn: str):
            return {"test1": f1, "test2": f2, "test3": f3}.get(cn)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.admin.FreeIPAFASAgreement.all", return_value=[listed1, listed2, listed3]),
            patch("core.admin.FreeIPAFASAgreement.get", side_effect=_fake_get),
        ):
            url = reverse("admin:auth_ipafasagreement_changelist")
            confirm = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["test1", "test2", "test3"],
                },
                follow=False,
            )
            self.assertEqual(confirm.status_code, 200)
            self.assertContains(confirm, "Are you sure", status_code=200)

            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["test1", "test2", "test3"],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Successfully deleted")
        self.assertNotContains(resp, "Failed to delete")
