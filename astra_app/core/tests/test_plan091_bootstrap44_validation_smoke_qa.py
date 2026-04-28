import json
import re
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import MembershipType
from core.tests.utils_test_data import ensure_core_categories


class Plan091Bootstrap44ValidationSmokeQaTests(TestCase):
    def _payload_from_html(self, html: str, script_attr: str) -> dict[str, object]:
        match = re.search(
            rf'<script type="application/json" {script_attr}>(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return json.loads(str(match.group(1)))

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Members",
                "group_cn": "mirror-members",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

    @override_settings(REGISTRATION_OPEN=True)
    def test_registration_required_fields_render_and_invalid_post_shows_bootstrap_feedback(self) -> None:
        resp = self.client.get(reverse("register"))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        self.assertIn('data-register-root=""', html)
        self.assertIn(f'data-register-api-url="{reverse("api-register-detail")}"', html)
        self.assertIn(f'data-register-submit-url="{reverse("register")}"', html)
        self.assertIn("data-registration-initial-payload", html)

        payload = self._payload_from_html(html, "data-registration-initial-payload")
        self.assertTrue(payload["registration_open"])
        self.assertFalse(payload["form"]["is_bound"])
        fields = {field["name"]: field for field in payload["form"]["fields"]}
        self.assertTrue(fields["username"]["required"])
        self.assertTrue(fields["first_name"]["required"])
        self.assertTrue(fields["last_name"]["required"])
        self.assertTrue(fields["email"]["required"])

        resp2 = self.client.post(
            reverse("register"),
            data={
                "username": "",
                "first_name": "",
                "last_name": "",
                "email": "",
                "invitation_token": "",
            },
            follow=False,
        )
        self.assertEqual(resp2.status_code, 200)

        html2 = resp2.content.decode("utf-8")
        self.assertIn('data-register-root=""', html2)

        payload2 = self._payload_from_html(html2, "data-registration-initial-payload")
        self.assertTrue(payload2["form"]["is_bound"])
        fields2 = {field["name"]: field for field in payload2["form"]["fields"]}
        self.assertIn("This field is required.", fields2["username"]["errors"])
        self.assertIn("This field is required.", fields2["first_name"]["errors"])
        self.assertIn("This field is required.", fields2["last_name"]["errors"])
        self.assertIn("This field is required.", fields2["email"]["errors"])
        self.assertIn("You must be over 16 years old to create an account", fields2["over_16"]["errors"])

    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_request_mirror_invalid_urls_show_bootstrap_feedback(self) -> None:
        self._login_as_freeipa("alice")

        mirror_type = MembershipType.objects.enabled().filter(category_id="mirror").first()
        self.assertIsNotNone(mirror_type)
        mirror_code = str(mirror_type.code)

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.views_membership.FreeIPAUser.get", return_value=alice),
            patch("core.views_membership.user.block_action_without_coc", return_value=None),
            patch("core.views_membership.user.block_action_without_country_code", return_value=None),
        ):
            resp = self.client.get(reverse("membership-request"))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'data-membership-request-form-root=""')
            self.assertContains(resp, f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"')
            self.assertContains(resp, f'data-membership-request-form-submit-url="{reverse("membership-request")}"')
            self.assertNotContains(resp, 'id="membership-request-form"')
            self.assertContains(resp, "core/js/form_validation_bootstrap44.js")

            resp2 = self.client.post(
                reverse("membership-request"),
                data={
                    "membership_type": mirror_code,
                    "q_domain": "not a url",
                    "q_pull_request": "also not a url",
                },
                follow=False,
            )

        self.assertEqual(resp2.status_code, 200)
        html2 = resp2.content.decode("utf-8")
        self.assertIn('data-membership-request-form-root=""', html2)

        payload2 = self._payload_from_html(html2, "data-membership-request-form-initial-payload")
        self.assertTrue(payload2["form"]["is_bound"])
        fields2 = {field["name"]: field for field in payload2["form"]["fields"]}
        self.assertEqual(mirror_code, fields2["membership_type"]["value"])
        self.assertIn("Enter a valid URL.", fields2["q_domain"]["errors"])
        self.assertIn("Enter a valid URL.", fields2["q_pull_request"]["errors"])
