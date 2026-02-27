
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class AdminPermissionGrantFormMediaTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_add_form_includes_principal_dropdown_js_and_fields(self) -> None:
        admin_username = "alice"
        freeipa_admin = FreeIPAUser(admin_username, {"uid": [admin_username], "memberof_group": ["admins"]})

        self._login_as_freeipa_admin(admin_username)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_admin),
            patch(
                "core.admin.FreeIPAUser.all",
                return_value=[
                    FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []}),
                    FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []}),
                ],
            ),
            patch(
                "core.admin.FreeIPAGroup.all",
                return_value=[
                    FreeIPAGroup("group-a"),
                    FreeIPAGroup("group-b"),
                ],
            ),
        ):
            url = reverse("admin:core_freeipapermissiongrant_add")
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "core/js/admin_permission_grants_principal_dropdown.js")
        self.assertContains(resp, 'id="id_principal_type"')
        self.assertContains(resp, 'id="id_principal_name"')
