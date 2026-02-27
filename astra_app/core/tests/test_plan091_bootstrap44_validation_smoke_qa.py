from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import MembershipType
from core.tests.utils_test_data import ensure_core_categories


class Plan091Bootstrap44ValidationSmokeQaTests(TestCase):
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
        self.assertIn("<form", html)
        self.assertIn("novalidate", html)
        self.assertIn("needs-validation", html)
        self.assertIn('data-required-indicator-for="id_username"', html)
        self.assertRegex(html, r"name=\"username\"[^>]*required")
        self.assertRegex(html, r"name=\"first_name\"[^>]*required")
        self.assertRegex(html, r"name=\"last_name\"[^>]*required")
        self.assertRegex(html, r"name=\"email\"[^>]*required")

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
        self.assertIn("invalid-feedback", html2)
        self.assertIn("is-invalid", html2)
        self.assertIn("This field is required", html2)
        self.assertIn("You must be over 16 years old", html2)

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
            self.assertContains(resp, 'id="membership-request-form"')
            self.assertContains(resp, 'data-validation-hook="membership-mirror"')
            self.assertContains(resp, "needs-validation")
            self.assertContains(resp, "novalidate")
            self.assertContains(resp, "core/js/form_validation_bootstrap44.js")
            self.assertContains(resp, "astra:validate-form")

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
        self.assertContains(resp2, "invalid-feedback")
        self.assertContains(resp2, "was-validated")
        self.assertIn("Enter a valid URL", resp2.content.decode("utf-8"))
