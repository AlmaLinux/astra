
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from post_office.models import EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipType
from core.permissions import ASTRA_ADD_SEND_MAIL
from core.tests.utils_test_data import ensure_core_categories


class EmailTemplatesUiTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _reviewer(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

    def test_requires_permission(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 302)

    def test_list_shows_templates(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer()

        EmailTemplate.objects.create(
            name="t-1",
            description="First",
            subject="Subj",
            content="Text",
            html_content="<p>Hi</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Templates")
        self.assertContains(resp, "t-1")
        self.assertContains(resp, "First")

    def test_templates_page_get_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("reviewer")
        EmailTemplate.objects.create(
            name="shell-template",
            description="Shell template",
            subject="Subject",
            content="Text",
            html_content="<p>HTML</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-email-templates-root=""')
        self.assertContains(resp, 'data-email-templates-api-url="/api/v1/email-tools/templates/detail"')
        self.assertContains(resp, f'data-email-template-create-url="{reverse("email-template-create")}"')
        self.assertContains(resp, "Loading templates...")

    def test_template_create_get_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get(reverse("email-template-create"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-email-template-editor-root=""')
        self.assertContains(resp, 'data-email-template-editor-api-url="/api/v1/email-tools/templates/new/detail"')
        self.assertContains(resp, f'data-email-template-list-url="{reverse("email-templates")}"')
        self.assertContains(resp, f'data-email-template-preview-url="{reverse("email-template-render-preview")}"')
        self.assertContains(resp, "Loading template editor...")

    def test_template_edit_get_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("reviewer")
        template = EmailTemplate.objects.create(
            name="shell-edit-template",
            description="Shell edit",
            subject="Subject",
            content="Text",
            html_content="<p>HTML</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get(reverse("email-template-edit", kwargs={"template_id": template.pk}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-email-template-editor-root=""')
        self.assertContains(resp, f'data-email-template-editor-api-url="/api/v1/email-tools/templates/{template.pk}/detail"')
        self.assertContains(resp, f'data-email-template-delete-url="{reverse("email-template-delete", kwargs={"template_id": template.pk})}"')
        self.assertContains(resp, f'data-email-template-submit-url="{reverse("email-template-edit", kwargs={"template_id": template.pk})}"')
        self.assertContains(resp, "Loading template editor...")

    def test_templates_detail_api_returns_data_only_payload(self) -> None:
        self._login_as_freeipa_user("reviewer")
        locked_name = settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME
        locked_template = EmailTemplate.objects.create(
            name=locked_name,
            description="Locked",
            subject="Locked subject",
            content="Locked text",
            html_content="<p>Locked</p>",
        )
        unlocked = EmailTemplate.objects.create(
            name="editable-template",
            description="Editable",
            subject="Editable subject",
            content="Editable text",
            html_content="<p>Editable</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get("/api/v1/email-tools/templates/detail")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn(
            {
                "id": unlocked.pk,
                "name": "editable-template",
                "description": "Editable",
                "is_locked": False,
            },
            payload["templates"],
        )
        self.assertIn(
            {
                "id": locked_template.pk,
                "name": locked_name,
                "description": "Locked",
                "is_locked": True,
            },
            payload["templates"],
        )
        self.assertNotIn("create_url", payload)
        self.assertNotIn("edit_url", payload)
        self.assertNotIn("delete_url", payload)
        self.assertNotIn("actions", payload)

    def test_template_create_detail_api_returns_data_only_payload(self) -> None:
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get("/api/v1/email-tools/templates/new/detail")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["mode"], "create")
        self.assertEqual(payload["template"], None)
        self.assertEqual(payload["compose"]["selected_template_id"], None)
        self.assertEqual(payload["compose"]["available_variables"], [])
        self.assertNotIn("back_url", payload)
        self.assertNotIn("preview_url", payload)

    def test_template_edit_detail_api_returns_data_only_payload(self) -> None:
        self._login_as_freeipa_user("reviewer")
        template = EmailTemplate.objects.create(
            name="editable-template",
            description="Editable",
            subject="Hello {{ username }}",
            content="Text body",
            html_content="<p>Hello {{ username }}</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get(f"/api/v1/email-tools/templates/{template.pk}/detail")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["mode"], "edit")
        self.assertEqual(
            payload["template"],
            {
                "id": template.pk,
                "name": "editable-template",
                "description": "Editable",
                "is_locked": False,
            },
        )
        self.assertEqual(payload["compose"]["selected_template_id"], template.pk)
        self.assertEqual(payload["compose"]["preview"]["subject"], "Hello -username-")
        self.assertNotIn("delete_url", payload)
        self.assertNotIn("list_url", payload)
        self.assertNotIn("page_title", payload)

    def test_execute_email_template_save_chooses_update_or_create_mutation(self) -> None:
        from core.templated_email import execute_email_template_save

        original = EmailTemplate.objects.create(
            name="shared-save-original",
            description="Original",
            subject="Original subject",
            content="Original text",
            html_content="<p>Original html</p>",
        )

        updated = execute_email_template_save(
            template=original,
            raw_name=None,
            subject="Updated subject",
            html_content="<p>Updated html</p>",
            text_content="Updated text",
        )

        self.assertEqual(updated.pk, original.pk)
        updated.refresh_from_db()
        self.assertEqual(updated.subject, "Updated subject")
        self.assertEqual(updated.html_content, "<p>Updated html</p>")
        self.assertEqual(updated.content, "Updated text")

        created = execute_email_template_save(
            template=None,
            raw_name="shared-save-created",
            subject="Created subject",
            html_content="<p>Created html</p>",
            text_content="Created text",
        )

        self.assertNotEqual(created.pk, original.pk)
        self.assertEqual(created.name, "shared-save-created")
        self.assertEqual(created.subject, "Created subject")
        self.assertEqual(created.html_content, "<p>Created html</p>")
        self.assertEqual(created.content, "Created text")

    def test_create_edit_delete_template(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-templates"))

        self.assertEqual(resp.status_code, 200)
        # Locked templates should not advertise a delete action in the UI.
        self.assertNotContains(resp, f"data-delete-url=\"{delete_url}\"")

    def test_edit_disables_name_field_for_locked_template(self) -> None:
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("email-template-edit", kwargs={"template_id": tpl.pk}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '"name": "name"')
        self.assertContains(resp, '"disabled": true')

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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
                "acceptance_template": tpl,
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
                "acceptance_template": tpl,
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

    def test_create_allows_long_subject(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        too_long_subject = "Action required: more information needed for your membership application"

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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
        self.assertNotContains(resp, "Subject is too long")
        tpl = EmailTemplate.objects.get(name="created-long-subject")
        self.assertEqual(tpl.subject, too_long_subject)

    def test_create_rejects_newline_subject(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-create"),
                data={
                    "name": "created-newline-subject",
                    "description": "Created",
                    "subject": "Action required:\nmore information needed",
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Email subjects cannot contain line breaks.")
        self.assertFalse(EmailTemplate.objects.filter(name="created-newline-subject").exists())

    def test_save_as_allows_long_subject(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        too_long_subject = "Action required: more information needed for your membership application"

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("email-template-save-as"),
                data={
                    "name": "saved-as-long-subject",
                    "subject": too_long_subject,
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(payload.get("name"), "saved-as-long-subject")

        from post_office.models import EmailTemplate

        tpl = EmailTemplate.objects.get(name="saved-as-long-subject")
        self.assertEqual(tpl.subject, too_long_subject)

    def test_save_and_save_as_reject_newline_subject(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})
        tpl = EmailTemplate.objects.create(
            name="saved-newline-subject",
            description="Saved",
            subject="Subj",
            content="Text",
            html_content="<p>Hello</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            save_resp = self.client.post(
                reverse("email-template-save"),
                data={
                    "email_template_id": str(tpl.pk),
                    "subject": "Action required:\r\nmore information needed",
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
            )

        self.assertEqual(save_resp.status_code, 400)
        self.assertEqual(save_resp.json()["error"], "Email subjects cannot contain line breaks.")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            save_as_resp = self.client.post(
                reverse("email-template-save-as"),
                data={
                    "name": "saved-as-newline-subject",
                    "subject": "Action required:\nmore information needed",
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                },
            )

        self.assertEqual(save_as_resp.status_code, 400)
        self.assertEqual(save_as_resp.json()["error"], "Email subjects cannot contain line breaks.")
        self.assertFalse(EmailTemplate.objects.filter(name="saved-as-newline-subject").exists())

    def test_template_render_preview_endpoint(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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
