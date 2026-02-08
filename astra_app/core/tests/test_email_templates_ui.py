
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipType
from core.permissions import ASTRA_ADD_SEND_MAIL


class EmailTemplatesUiTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def test_requires_permission(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 302)

    def test_list_shows_templates(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        EmailTemplate.objects.create(
            name="t-1",
            description="First",
            subject="Subj",
            content="Text",
            html_content="<p>Hi</p>",
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Templates")
        self.assertContains(resp, "t-1")
        self.assertContains(resp, "First")

    def test_create_edit_delete_template(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            create_resp = self.client.post(
                reverse("email-template-create"),
                data={
                    "name": "created-1",
                    "description": "Created",
                    "subject": "Hello",
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
                follow=True,
            )

        self.assertEqual(create_resp.status_code, 200)
        tpl = EmailTemplate.objects.get(name="created-1")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            edit_resp = self.client.post(
                reverse("email-template-edit", kwargs={"template_id": tpl.pk}),
                data={
                    "name": "created-1",
                    "description": "Updated",
                    "subject": "Updated subj",
                    "html_content": "<p>Updated</p>",
                    "text_content": "Updated",
                },
                follow=True,
            )

        self.assertEqual(edit_resp.status_code, 200)
        tpl.refresh_from_db()
        self.assertEqual(tpl.description, "Updated")
        self.assertEqual(tpl.subject, "Updated subj")
        self.assertEqual(tpl.content, "Updated")
        self.assertEqual(tpl.html_content, "<p>Updated</p>")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            delete_resp = self.client.post(
                reverse("email-template-delete", kwargs={"template_id": tpl.pk}),
                follow=True,
            )

        self.assertEqual(delete_resp.status_code, 200)
        self.assertFalse(EmailTemplate.objects.filter(pk=tpl.pk).exists())

    def test_cannot_delete_template_referenced_by_settings(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        locked_name = settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME
        tpl, _ = EmailTemplate.objects.update_or_create(
            name=locked_name,
            defaults={
                "description": "Locked",
                "subject": "Subj",
                "content": "Text",
                "html_content": "<p>Text</p>",
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-delete", kwargs={"template_id": tpl.pk}),
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(EmailTemplate.objects.filter(pk=tpl.pk).exists())
        self.assertContains(resp, "cannot be deleted")

    def test_list_hides_delete_action_for_locked_template(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        locked_name = settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME
        tpl, _ = EmailTemplate.objects.update_or_create(
            name=locked_name,
            defaults={
                "description": "Locked",
                "subject": "Subj",
                "content": "Text",
                "html_content": "<p>Text</p>",
            },
        )

        delete_url = reverse("email-template-delete", kwargs={"template_id": tpl.pk})

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 200)
        # Locked templates should not advertise a delete action in the UI.
        self.assertNotContains(resp, f"data-delete-url=\"{delete_url}\"")

    def test_edit_disables_name_field_for_locked_template(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        locked_name = settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME
        tpl, _ = EmailTemplate.objects.update_or_create(
            name=locked_name,
            defaults={
                "description": "Locked",
                "subject": "Subj",
                "content": "Text",
                "html_content": "<p>Text</p>",
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-template-edit", kwargs={"template_id": tpl.pk}))

        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        marker = 'id="id_name"'
        idx = html.find(marker)
        self.assertNotEqual(idx, -1, "Expected name input to render")
        start = html.rfind("<input", 0, idx)
        end = html.find(">", idx)
        self.assertNotEqual(start, -1)
        self.assertNotEqual(end, -1)
        name_input_tag = html[start : end + 1]
        self.assertIn("disabled", name_input_tag)

    def test_cannot_rename_template_referenced_by_settings(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        locked_name = settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME
        tpl, _ = EmailTemplate.objects.update_or_create(
            name=locked_name,
            defaults={
                "description": "Locked",
                "subject": "Subj",
                "content": "Text",
                "html_content": "<p>Text</p>",
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-edit", kwargs={"template_id": tpl.pk}),
                data={
                    "name": f"{locked_name}-renamed",
                    "description": "Locked",
                    "subject": "Subj",
                    "html_content": "<p>Text</p>",
                    "text_content": "Text",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        tpl.refresh_from_db()
        self.assertEqual(tpl.name, locked_name)
        self.assertContains(resp, "cannot be renamed")

    def test_cannot_delete_template_referenced_by_membership_type(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        tpl = EmailTemplate.objects.create(
            name="membership-acceptance-locked",
            description="Locked",
            subject="Subj",
            content="Text",
            html_content="<p>Text</p>",
        )

        MembershipType.objects.update_or_create(
            code="individual_acceptance_locked",
            defaults={
                "name": "Individual",
                "votes": 1,
                "group_cn": "",  # not relevant for this test
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
                "acceptance_template": tpl,
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-delete", kwargs={"template_id": tpl.pk}),
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(EmailTemplate.objects.filter(pk=tpl.pk).exists())
        self.assertContains(resp, "cannot be deleted")

    def test_cannot_rename_template_referenced_by_membership_type(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        tpl = EmailTemplate.objects.create(
            name="membership-acceptance-locked-rename",
            description="Locked",
            subject="Subj",
            content="Text",
            html_content="<p>Text</p>",
        )

        MembershipType.objects.update_or_create(
            code="individual_acceptance_locked_rename",
            defaults={
                "name": "Individual",
                "votes": 1,
                "group_cn": "",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
                "acceptance_template": tpl,
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-edit", kwargs={"template_id": tpl.pk}),
                data={
                    "name": "membership-acceptance-locked-rename-new",
                    "description": "Locked",
                    "subject": "Subj",
                    "html_content": "<p>Text</p>",
                    "text_content": "Text",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        tpl.refresh_from_db()
        self.assertEqual(tpl.name, "membership-acceptance-locked-rename")
        self.assertContains(resp, "cannot be renamed")

    def test_create_rejects_subject_that_would_be_header_folded(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        too_long_subject = "Action required: more information needed for your membership application"

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-create"),
                data={
                    "name": "created-long-subject",
                    "description": "Created",
                    "subject": too_long_subject,
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Subject is too long")
        self.assertFalse(EmailTemplate.objects.filter(name="created-long-subject").exists())

    def test_save_as_rejects_subject_that_would_be_header_folded(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        too_long_subject = "Action required: more information needed for your membership application"

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-save-as"),
                data={
                    "name": "saved-as-long-subject",
                    "subject": too_long_subject,
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
            )

        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertEqual(payload.get("ok"), False)
        self.assertIn("Subject is too long", str(payload.get("error", "")))

    def test_template_render_preview_endpoint(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-render-preview"),
                data={
                    "subject": "Hi {{ name }}",
                    "html_content": "<p>{{ name }}</p>",
                    "text_content": "{{ name }}",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["subject"], "Hi -name-")
        self.assertEqual(payload["html"], "<p>-name-</p>")
        self.assertEqual(payload["text"], "-name-")

    def test_template_render_preview_endpoint_rewrites_inline_image_tag_to_url(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        image_url = "http://localhost:9000/astra-media/mail-images/logo.png"
        html = (
            "{% load post_office %}\n"
            "<p><em>The AlmaLinux Team</em></p>\n"
            f"<img src=\"{{% inline_image '{image_url}' %}}\" />\n"
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-render-preview"),
                data={
                    "subject": "Hello",
                    "html_content": html,
                    "text_content": "Plain text",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn(image_url, payload.get("html", ""))
        self.assertNotIn("{% inline_image", payload.get("html", ""))

    def test_edit_page_uses_local_codemirror_assets(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        tpl = EmailTemplate.objects.create(
            name="t-1",
            description="First",
            subject="Subj",
            content="Text",
            html_content="<p>Hi</p>",
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-template-edit", kwargs={"template_id": tpl.pk}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/static/core/vendor/codemirror/codemirror.min.css"')
        self.assertContains(resp, 'href="/static/core/vendor/codemirror/mdn-like.min.css"')
        self.assertContains(resp, 'src="/static/core/vendor/codemirror/codemirror.min.js"')
        self.assertContains(resp, 'src="/static/core/vendor/codemirror/xml.min.js"')
        self.assertContains(resp, 'src="/static/core/vendor/codemirror/javascript.min.js"')
        self.assertContains(resp, 'src="/static/core/vendor/codemirror/css.min.js"')
        self.assertContains(resp, 'src="/static/core/vendor/codemirror/htmlmixed.min.js"')
        self.assertContains(resp, 'src="/static/core/vendor/codemirror/overlay.min.js"')
        self.assertNotContains(resp, "cdnjs.cloudflare.com/ajax/libs/codemirror")
