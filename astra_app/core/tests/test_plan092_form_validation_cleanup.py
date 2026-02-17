from unittest.mock import patch

from django import forms
from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.forms_base import StyledForm
from core.forms_registration import ResendRegistrationEmailForm
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_SEND_MAIL
from core.views_account_invitations import AccountInvitationUploadForm
from core.views_send_mail import SendMailForm
from core.views_templated_email import EmailTemplateManageForm


class _PartialRenderForm(StyledForm):
    text = forms.CharField(required=True)
    checkbox = forms.BooleanField(required=True)
    grouped = forms.CharField(required=True)
    datalist = forms.CharField(required=True, widget=forms.TextInput(attrs={"list": "timezone-options"}))


class Plan092FormValidationCleanupTests(SimpleTestCase):
    def test_migrated_public_forms_inherit_styled_form(self) -> None:
        self.assertTrue(issubclass(EmailTemplateManageForm, StyledForm))
        self.assertTrue(issubclass(AccountInvitationUploadForm, StyledForm))
        self.assertTrue(issubclass(SendMailForm, StyledForm))
        self.assertTrue(issubclass(ResendRegistrationEmailForm, StyledForm))

    def test_field_variant_partials_render_invalid_feedback_without_d_block(self) -> None:
        form = _PartialRenderForm(
            data={
                "text": "",
                "checkbox": "",
                "grouped": "",
                "datalist": "",
            }
        )
        form.is_valid()

        inner_html = Template("{% include 'core/_form_field_inner.html' with field=form.text %}").render(
            Context({"form": form})
        )
        checkbox_html = Template("{% include 'core/_form_field_checkbox.html' with field=form.checkbox %}").render(
            Context({"form": form})
        )
        input_group_html = Template(
            "{% include 'core/_form_field_input_group.html' with field=form.grouped prefix='@' %}"
        ).render(Context({"form": form}))
        datalist_html = Template(
            "{% include 'core/_form_field_datalist.html' with field=form.datalist datalist_id='timezone-options' datalist_options=datalist_options %}"
        ).render(Context({"form": form, "datalist_options": [("UTC", "UTC")]}))

        for html in [inner_html, checkbox_html, input_group_html, datalist_html]:
            self.assertIn("invalid-feedback", html)
            self.assertNotIn("invalid-feedback d-block", html)
            self.assertIn("is-invalid", html)


class Plan092SendMailValidationIntegrationTests(TestCase):
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

    def test_send_mail_invalid_post_shows_bootstrap_feedback_without_d_block(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "user@example.com",
                    "reply_to": "not-an-email",
                },
            )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("invalid-feedback", html)
        self.assertNotIn("invalid-feedback d-block", html)
        self.assertIn("is-invalid", html)
