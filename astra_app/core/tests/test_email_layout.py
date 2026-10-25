import io
import os
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from post_office.models import EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_SEND_MAIL

_LOGO_URL = "http://localhost:9000/astra-media/mail-images/logo.png"

# Minimal valid 1x1 PNG for MIME type detection in inline-image rendering.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_LAYOUT_HTML = (
    "<html><head><style>.content p { color: #123456; }</style></head><body>"
    "<table><tr><td>LAYOUT-HEADER</td></tr>"
    '<tr><td class="content">{% block content %}{% endblock %}</td></tr></table>'
    "</body></html>"
)

_CHILD_HTML = '{% extends "email-layout" %}{% block content %}<p>Hi {{ name }}</p>{% endblock %}'


def _set_layout(html_content: str) -> None:
    from core.templated_email import EMAIL_LAYOUT_TEMPLATE_NAME

    EmailTemplate.objects.update_or_create(
        name=EMAIL_LAYOUT_TEMPLATE_NAME,
        defaults={"subject": "", "content": "", "html_content": html_content},
    )


class EmailLayoutRenderingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        _set_layout(_LAYOUT_HTML)

    def test_preview_renders_child_inside_layout_with_inlined_styles(self) -> None:
        from core.templated_email import render_templated_email_preview

        rendered = render_templated_email_preview(
            subject="Subject",
            html_content=_CHILD_HTML,
            text_content="Hi {{ name }}",
            context={"name": "Alice"},
        )

        self.assertIn("LAYOUT-HEADER", rendered["html"])
        self.assertIn('<p style="color: #123456;">Hi Alice</p>', rendered["html"])
        self.assertEqual(rendered["text"], "Hi Alice")

    def test_layout_edits_apply_to_next_render(self) -> None:
        from core.templated_email import render_templated_email_preview

        render_templated_email_preview(subject="", html_content=_CHILD_HTML, text_content="", context={})
        _set_layout(_LAYOUT_HTML.replace("LAYOUT-HEADER", "NEW-HEADER"))

        rendered = render_templated_email_preview(subject="", html_content=_CHILD_HTML, text_content="", context={})

        self.assertIn("NEW-HEADER", rendered["html"])

    def test_unknown_parent_is_a_template_error(self) -> None:
        from core.templated_email import render_templated_email_preview

        with self.assertRaises(ValueError):
            render_templated_email_preview(
                subject="",
                html_content='{% extends "no-such-layout" %}{% block content %}x{% endblock %}',
                text_content="",
                context={},
            )

    def test_queue_composed_email_sends_child_inside_layout_with_inlined_styles(self) -> None:
        from core.templated_email import queue_composed_email

        email = queue_composed_email(
            recipients=["alice@example.com"],
            sender="noreply@example.com",
            subject_source="Hello",
            text_source="Hi {{ name }}",
            html_source=_CHILD_HTML,
            context={"name": "Alice"},
            commit=False,
        )

        self.assertIn("LAYOUT-HEADER", email.html_message)
        self.assertIn('<p style="color: #123456;">Hi Alice</p>', email.html_message)

    def test_templated_email_is_delivered_as_rendered(self) -> None:
        """post_office re-renders the raw template at delivery when an Email has both
        `template` and `context`, which would drop staged images, inlined CSS and
        system context; delivery must use the stored rendering."""
        from core.templated_email import bulk_save_emails, queue_templated_email

        EmailTemplate.objects.create(name="layout-child", subject="Hello", content="Hi", html_content=_CHILD_HTML)
        email = queue_templated_email(
            recipients=["alice@example.com"],
            sender="noreply@example.com",
            template_name="layout-child",
            context={"name": "Alice"},
            commit=False,
        )
        with patch("post_office.models.Email.dispatch"):
            bulk_save_emails([email])

        delivered_html = email.email_message().alternatives[0].content
        self.assertEqual(delivered_html, email.html_message)
        self.assertEqual(email.context["template_name"], "layout-child")

    def test_queue_composed_email_leaves_fragments_without_styles_untouched(self) -> None:
        from core.templated_email import queue_composed_email

        email = queue_composed_email(
            recipients=["alice@example.com"],
            sender="noreply@example.com",
            subject_source="Hello",
            text_source="Hi",
            html_source="<p>Hi {{ name }}</p>",
            context={"name": "Alice"},
            commit=False,
        )

        self.assertEqual(email.html_message, "<p>Hi Alice</p>")


@override_settings(AWS_STORAGE_BUCKET_NAME="astra-media")
class EmailLayoutInlineImageTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        _set_layout(
            "{% load post_office %}"
            f"<img src=\"{{% inline_image '{_LOGO_URL}' %}}\" />"
            "{% block content %}{% endblock %}"
        )

    def test_sending_embeds_layout_inline_image_and_cleans_up_temp_file(self) -> None:
        from core.templated_email import queue_composed_email

        with (
            patch("core.templated_email.default_storage.open", return_value=io.BytesIO(_PNG_BYTES)),
            patch("core.templated_email.os.unlink", wraps=os.unlink) as unlink_mock,
            patch("post_office.models.Email.dispatch"),
        ):
            email = queue_composed_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                subject_source="Hello",
                text_source="Hi",
                html_source=_CHILD_HTML,
                context={"name": "Alice"},
            )

        self.assertIn('src="cid:', email.html_message)
        image_parts = [part for part in email.email_message().message().walk() if part.get_content_maintype() == "image"]
        self.assertEqual([part.get_payload(decode=True) for part in image_parts], [_PNG_BYTES])

        unlink_mock.assert_called_once()
        self.assertFalse(os.path.exists(unlink_mock.call_args.args[0]))

    def test_preview_shows_layout_inline_image_as_url(self) -> None:
        from core.templated_email import render_templated_email_preview

        rendered = render_templated_email_preview(subject="", html_content=_CHILD_HTML, text_content="", context={})

        self.assertIn(f'src="{_LOGO_URL}"', rendered["html"])

    def test_failed_send_does_not_leak_staging_into_later_renders(self) -> None:
        from core.templated_email import queue_composed_email, render_templated_email_preview

        with (
            patch("core.templated_email.default_storage.open", return_value=io.BytesIO(_PNG_BYTES)),
            patch("core.templated_email.bulk_save_emails", side_effect=RuntimeError("db down")),
            self.assertRaises(RuntimeError),
        ):
            queue_composed_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                subject_source="Hello",
                text_source="Hi",
                html_source=_CHILD_HTML,
                context={},
            )

        rendered = render_templated_email_preview(subject="", html_content=_CHILD_HTML, text_content="", context={})
        self.assertIn(f'src="{_LOGO_URL}"', rendered["html"])


class EmailTemplateSaveValidationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        _set_layout(_LAYOUT_HTML)
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        session = self.client.session
        session["_freeipa_username"] = "reviewer"
        session.save()
        self.reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

    def _save_json(self, *, template: EmailTemplate, html_content: str, text_content: str = "Hi"):
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer):
            return self.client.post(
                reverse("email-template-save"),
                data={
                    "email_template_id": str(template.pk),
                    "subject": "Subject",
                    "html_content": html_content,
                    "text_content": text_content,
                },
            )

    def test_editor_form_rejects_template_syntax_error(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer):
            resp = self.client.post(
                reverse("email-template-create"),
                data={
                    "name": "broken-syntax",
                    "subject": "Subject",
                    "html_content": "<p>{% if %}</p>",
                    "text_content": "Hi",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Template error")
        self.assertFalse(EmailTemplate.objects.filter(name="broken-syntax").exists())

    def test_editor_form_saves_valid_child_template(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer):
            self.client.post(
                reverse("email-template-create"),
                data={
                    "name": "valid-child",
                    "subject": "Subject",
                    "html_content": _CHILD_HTML,
                    "text_content": "Hi {{ name }}",
                },
            )

        self.assertTrue(EmailTemplate.objects.filter(name="valid-child").exists())

    def test_json_save_rejects_unknown_parent(self) -> None:
        tpl = EmailTemplate.objects.create(name="child", subject="S", content="Hi", html_content="<p>Hi</p>")

        resp = self._save_json(
            template=tpl,
            html_content='{% extends "no-such-layout" %}{% block content %}x{% endblock %}',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("no-such-layout", resp.json()["error"])
        tpl.refresh_from_db()
        self.assertEqual(tpl.html_content, "<p>Hi</p>")

    def test_json_save_rejects_layout_without_content_block(self) -> None:
        from core.templated_email import EMAIL_LAYOUT_TEMPLATE_NAME

        layout = EmailTemplate.objects.get(name=EMAIL_LAYOUT_TEMPLATE_NAME)

        resp = self._save_json(template=layout, html_content="<html><body>No slot</body></html>", text_content="")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("{% block content %}", resp.json()["error"])
        layout.refresh_from_db()
        self.assertEqual(layout.html_content, _LAYOUT_HTML)

    def test_json_save_rejects_extends_in_plain_text(self) -> None:
        tpl = EmailTemplate.objects.create(name="child", subject="S", content="Hi", html_content="<p>Hi</p>")

        resp = self._save_json(template=tpl, html_content=_CHILD_HTML, text_content=_CHILD_HTML)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("plain-text", resp.json()["error"])

    def test_new_template_starts_from_layout(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer):
            resp = self.client.get("/api/v1/email-tools/templates/new/detail")

        fields = {field["name"]: field for field in resp.json()["form"]["fields"]}
        self.assertTrue(fields["html_content"]["value"].startswith('{% extends "email-layout" %}'))

    def test_send_mail_does_not_offer_layout(self) -> None:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get("/api/v1/email-tools/send-mail/detail")

        self.assertNotIn("email-layout", [template["name"] for template in resp.json()["templates"]])

    def test_send_mail_does_not_prefill_layout_from_query_param(self) -> None:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=manual&to=jim@example.com&template=email-layout")

        self.assertContains(resp, "Email template not found")
        self.assertNotContains(resp, "LAYOUT-HEADER")

    def test_layout_cannot_be_deleted(self) -> None:
        from core.templated_email import EMAIL_LAYOUT_TEMPLATE_NAME

        layout = EmailTemplate.objects.get(name=EMAIL_LAYOUT_TEMPLATE_NAME)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer):
            self.client.post(reverse("email-template-delete", kwargs={"template_id": layout.pk}))

        self.assertTrue(EmailTemplate.objects.filter(pk=layout.pk).exists())


class EmailLayoutMigrationTests(TestCase):
    def test_wraps_fragments_and_leaves_full_documents_alone(self) -> None:
        from core.templated_email import EMAIL_LAYOUT_TEMPLATE_NAME

        _set_layout(_LAYOUT_HTML)
        fragment = EmailTemplate.objects.create(name="fragment", html_content="<p>Hi</p>")
        document = EmailTemplate.objects.create(name="document", html_content="<html><body>Hi</body></html>")
        text_only = EmailTemplate.objects.create(name="text-only", content="Hi", html_content="")

        migration = import_module("core.migrations.0121_email_layout_template")
        migration.wrap_email_templates_in_layout(apps, None)
        migration.wrap_email_templates_in_layout(apps, None)

        fragment.refresh_from_db()
        self.assertEqual(
            fragment.html_content,
            '{% extends "email-layout" %}{% block content %}\n<p>Hi</p>\n{% endblock %}',
        )
        document.refresh_from_db()
        self.assertEqual(document.html_content, "<html><body>Hi</body></html>")
        text_only.refresh_from_db()
        self.assertEqual(text_only.html_content, "")
        self.assertEqual(EmailTemplate.objects.get(name=EMAIL_LAYOUT_TEMPLATE_NAME).html_content, _LAYOUT_HTML)

        migration.unwrap_email_templates_from_layout(apps, None)
        fragment.refresh_from_db()
        self.assertEqual(fragment.html_content, "<p>Hi</p>")

    def test_template_name_moves_into_context_for_prerendered_emails(self) -> None:
        from post_office.models import Email

        template = EmailTemplate.objects.create(name="notice", subject="S", content="T", html_content="<p>T</p>")
        rendered = Email.objects.create(
            from_email="a@example.com", to=["b@example.com"], subject="S", message="T",
            template=template, context={"membership_type_code": "gold"},
        )
        render_on_delivery = Email.objects.create(
            from_email="a@example.com", to=["b@example.com"], template=template, context={},
        )

        import_module("core.migrations.0122_email_template_name_in_context").move_template_name_into_context(apps, None)

        rendered.refresh_from_db()
        self.assertIsNone(rendered.template)
        self.assertEqual(rendered.context, {"membership_type_code": "gold", "template_name": "notice"})
        render_on_delivery.refresh_from_db()
        self.assertEqual(render_on_delivery.template, template)

    @override_settings(
        ACCOUNT_DELETION_APPROVED_EMAIL_TEMPLATE_NAME="deletion",
        MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME="pending",
        MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME="rejected",
    )
    def test_custom_footers_replace_or_extend_the_default(self) -> None:
        from core.templated_email import EMAIL_LAYOUT_TEMPLATE_NAME, queue_composed_email

        EmailTemplate.objects.filter(name=EMAIL_LAYOUT_TEMPLATE_NAME).delete()
        layout_migration = import_module("core.migrations.0121_email_layout_template")
        layout_migration.create_email_layout_template(apps, None)
        for name in ["deletion", "pending", "rejected", "unrelated"]:
            EmailTemplate.objects.create(name=name, html_content=f"{layout_migration.WRAP_PREFIX}<p>Body</p>{layout_migration.WRAP_SUFFIX}")
        overridden = EmailTemplate.objects.create(
            name="custom",
            html_content='{% extends "email-layout" %}{% block footer %}Mine{% endblock %}',
        )

        migration = import_module("core.migrations.0123_email_layout_custom_footers")
        migration.add_custom_footers(apps, None)
        migration.add_custom_footers(apps, None)

        def footer(name: str, context: dict[str, object]) -> str:
            # A real send, not the preview: the preview fills placeholders for every
            # variable, so `{% if organization_name %}` would always be true there.
            html = queue_composed_email(
                recipients=["a@example.com"],
                sender="noreply@example.com",
                subject_source="",
                text_source="",
                html_source=EmailTemplate.objects.get(name=name).html_content,
                context=context,
                commit=False,
            ).html_message
            return html[html.index('class="footer"') :]

        self.assertIn("request to delete your AlmaLinux account was approved", footer("deletion", {"username": "a"}))
        self.assertNotIn("manage it", footer("deletion", {"username": "a"}))
        self.assertIn("you review membership requests", footer("pending", {}))
        self.assertNotIn("safely ignore", footer("pending", {}))
        self.assertRegex(
            footer("rejected", {"organization_name": "Example Org"}),
            r'as a contact for <a href="[^"]*/organizations/"[^>]*>Example Org</a>',
        )
        self.assertIn("you have an AlmaLinux account.", footer("rejected", {"username": "a"}))
        self.assertIn("safely ignore", footer("unrelated", {}))
        self.assertEqual(EmailTemplate.objects.get(name="custom").html_content, overridden.html_content)

        migration.remove_custom_footers(apps, None)
        self.assertEqual(
            EmailTemplate.objects.get(name="deletion").html_content,
            f"{layout_migration.WRAP_PREFIX}<p>Body</p>{layout_migration.WRAP_SUFFIX}",
        )

    def test_wrapped_template_renders_in_seeded_layout(self) -> None:
        from core.templated_email import EMAIL_LAYOUT_TEMPLATE_NAME, render_templated_email_preview

        EmailTemplate.objects.filter(name=EMAIL_LAYOUT_TEMPLATE_NAME).delete()
        template = EmailTemplate.objects.create(
            name="fragment",
            subject="Hello {{ name }}",
            content="Hi {{ name }}",
            html_content="<p>Hi {{ name }}</p>\n<p><em>The AlmaLinux Team</em></p>",
        )

        migration = import_module("core.migrations.0121_email_layout_template")
        migration.create_email_layout_template(apps, None)
        migration.wrap_email_templates_in_layout(apps, None)
        template.refresh_from_db()

        rendered = render_templated_email_preview(
            subject=template.subject,
            html_content=template.html_content,
            text_content=template.content,
            context={"name": "Alice"},
        )

        self.assertIn(migration.LOGO_STORAGE_KEY, rendered["html"])
        self.assertIn("If you weren't expecting this email, you can safely ignore it.", rendered["html"])
        self.assertNotIn("you have an AlmaLinux account", rendered["html"])
        self.assertRegex(rendered["html"], r'<p style="[^"]*margin: 0 0 16px[^"]*">Hi Alice</p>')

        account_holder = render_templated_email_preview(
            subject=template.subject,
            html_content=template.html_content,
            text_content=template.content,
            context={"name": "Alice", "username": "alice"},
        )
        self.assertIn("you have an AlmaLinux account.", account_holder["html"])
        # Usernames are not exposed in the footer.
        self.assertNotIn("alice", account_holder["html"])
        self.assertNotIn("you can safely ignore it", account_holder["html"])
