from typing import override
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class OrganizationFormTabValidationTests(TestCase):
    @override
    def setUp(self) -> None:
        ensure_core_categories()
        ensure_email_templates()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _valid_org_payload(self, *, name: str) -> dict[str, str]:
        return {
            "name": name,
            "country_code": "US",
            "business_contact_name": "Business",
            "business_contact_email": "business@example.com",
            "business_contact_phone": "",
            "pr_marketing_contact_name": "Marketing",
            "pr_marketing_contact_email": "marketing@example.com",
            "pr_marketing_contact_phone": "",
            "technical_contact_name": "Tech",
            "technical_contact_email": "tech@example.com",
            "technical_contact_phone": "",
            "website_logo": "https://example.com/logo-options",
            "website": "https://example.com/",
        }

    def test_post_invalid_technical_contact_email_activates_technical_tab(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Invalid Technical Email Org")
        payload["technical_contact_email"] = "invalid-email"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="tab-pane fade active show"\n          id="contacts-technical"', html=False)

    def test_post_invalid_business_contact_email_activates_business_tab(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Invalid Business Email Org")
        payload["business_contact_email"] = "invalid-email"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="tab-pane fade active show"\n          id="contacts-business"', html=False)

    def test_post_with_non_contact_errors_keeps_default_business_tab(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Invalid Country Org")
        payload["country_code"] = "ZZ"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="tab-pane fade active show"\n          id="contacts-business"', html=False)

    def test_org_form_includes_tab_switch_script(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.get(reverse("organization-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "contacts-tab-content")
        self.assertContains(response, "contacts-tabs")
        self.assertContains(response, "querySelector(':invalid')")
        self.assertContains(response, "'alx-tab-error'")
        self.assertContains(response, "scrollIntoView")
        self.assertContains(response, "focus()")
        self.assertContains(response, "select2('open')")
        self.assertContains(response, "scrollTarget")
        self.assertContains(response, "selection || adjacentContainer || field")
