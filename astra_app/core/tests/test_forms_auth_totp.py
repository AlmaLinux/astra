
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from core.forms_auth import ExpiredPasswordChangeForm, FreeIPAAuthenticationForm, PasswordResetSetForm
from core.forms_security import (
    PasswordConfirmationMixin,
    make_otp_field,
    make_password_confirmation_field,
    make_password_field,
)


class FreeIPAAuthenticationFormTests(TestCase):
    def test_appends_otp_to_password(self):
        request = RequestFactory().post("/login/")

        user = SimpleNamespace(is_active=True)

        with patch("django.contrib.auth.forms.authenticate", autospec=True) as authenticate:
            authenticate.return_value = user
            form = FreeIPAAuthenticationForm(
                request=request,
                data={
                    "username": "alice",
                    "password": "pw",
                    "otp": "123456",
                },
            )
            self.assertTrue(form.is_valid())

            authenticate.assert_called_with(request, username="alice", password="pw123456")


class AuthPasswordFormsConsolidationTests(TestCase):
    def test_password_forms_use_shared_confirmation_mixin(self) -> None:
        self.assertTrue(issubclass(ExpiredPasswordChangeForm, PasswordConfirmationMixin))
        self.assertTrue(issubclass(PasswordResetSetForm, PasswordConfirmationMixin))

    def test_password_reset_set_form_uses_shared_field_primitives(self) -> None:
        form = PasswordResetSetForm()
        expected_password = make_password_field(label="New password", min_length=6, max_length=122)
        expected_confirm = make_password_confirmation_field(label="Confirm new password", min_length=6, max_length=122)
        expected_otp = make_otp_field(
            help_text="Only required if your account has two-factor authentication enabled.",
            autocomplete="off",
        )

        self.assertEqual(form.fields["password"].label, expected_password.label)
        self.assertEqual(form.fields["password"].min_length, expected_password.min_length)
        self.assertEqual(form.fields["password"].max_length, expected_password.max_length)

        self.assertEqual(form.fields["password_confirm"].label, expected_confirm.label)
        self.assertEqual(form.fields["password_confirm"].min_length, expected_confirm.min_length)
        self.assertEqual(form.fields["password_confirm"].max_length, expected_confirm.max_length)

        self.assertEqual(form.fields["otp"].label, expected_otp.label)
        self.assertEqual(form.fields["otp"].required, expected_otp.required)
        self.assertEqual(form.fields["otp"].help_text, expected_otp.help_text)
        self.assertEqual(form.fields["otp"].widget.attrs.get("autocomplete"), expected_otp.widget.attrs.get("autocomplete"))
