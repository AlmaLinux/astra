from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP


class OrganizationFormVueTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_organization_create_page_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.get(reverse("organization-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-organization-form-root")
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/organizationForm.ts"')

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_organization_edit_page_renders_vue_shell_contract(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        organization = Organization.objects.create(name="Acme Org", representative="alice")
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice Example"], "memberof_group": [], "c": ["US"]})

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=fake_get):
            response = self.client.get(reverse("organization-edit", args=[organization.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-organization-form-root")
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/organizationForm.ts"')
