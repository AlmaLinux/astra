from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import Organization
from core.organization_claim import make_organization_claim_token


class OrganizationClaimVueTests(TestCase):
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
    def test_organization_claim_ready_page_renders_vue_shell_contract(self) -> None:
        organization = Organization.objects.create(
            name="Claimable Org",
            business_contact_email="contact@example.com",
        )
        token = make_organization_claim_token(organization)
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_organizations.block_action_without_coc", return_value=None),
            patch("core.views_organizations.block_action_without_country_code", return_value=None),
        ):
            response = self.client.get(reverse("organization-claim", args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-organization-claim-root")
        self.assertContains(response, 'data-claim-state="ready"')
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/organizationClaim.ts"')

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
    def test_organization_claim_invalid_page_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            response = self.client.get(reverse("organization-claim", args=["not-a-valid-token"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-organization-claim-root")
        self.assertContains(response, 'data-claim-state="invalid"')
