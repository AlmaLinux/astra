
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_email_identifier_resolves_to_username_before_auth(self) -> None:
        request = RequestFactory().post("/login/")
        user = SimpleNamespace(is_active=True)

        with (
            patch("core.forms_auth.FreeIPAUser.find_by_email", autospec=True) as find_by_email,
            patch("core.forms_auth.FreeIPAUser.get", side_effect=AssertionError("login form should not do a second caller-local lookup")),
            patch("django.contrib.auth.forms.authenticate", autospec=True) as authenticate,
        ):
            find_by_email.return_value = SimpleNamespace(username="alice")
            authenticate.return_value = user

            form = FreeIPAAuthenticationForm(
                request=request,
                data={
                    "username": "alice@example.com",
                    "password": "pw",
                    "otp": "",
                },
            )

            self.assertTrue(form.is_valid())
            self.assertEqual(find_by_email.call_count, 1)
            self.assertEqual(find_by_email.call_args.args[-1], "alice@example.com")
            authenticate.assert_called_once_with(request, username="alice", password="pw")

    def test_otp_retry_falls_back_to_plain_password_when_combined_fails(self) -> None:
        request = RequestFactory().post("/login/")
        user = SimpleNamespace(is_active=True)

        otp_lookup_client = SimpleNamespace(otptoken_find=Mock(return_value={"result": []}))

        with (
            patch("core.forms_auth.FreeIPAUser.get_client", autospec=True, return_value=otp_lookup_client),
            patch("django.contrib.auth.forms.authenticate", autospec=True) as authenticate,
        ):
            authenticate.side_effect = [None, user]

            form = FreeIPAAuthenticationForm(
                request=request,
                data={
                    "username": "alice",
                    "password": "pw",
                    "otp": "123456",
                },
            )

            self.assertTrue(form.is_valid())
            self.assertEqual(authenticate.call_count, 2)
            self.assertEqual(authenticate.call_args_list[0].kwargs, {"username": "alice", "password": "pw123456"})
            self.assertEqual(authenticate.call_args_list[1].kwargs, {"username": "alice", "password": "pw"})
            otp_lookup_client.otptoken_find.assert_called_once_with(o_ipatokenowner="alice", o_all=True)

    def test_otp_retry_does_not_fallback_when_otp_tokens_exist(self) -> None:
        request = RequestFactory().post("/login/")

        otp_lookup_client = SimpleNamespace(
            otptoken_find=Mock(return_value={"result": [{"ipatokenuniqueid": ["token-1"]}]})
        )

        with (
            patch("core.forms_auth.FreeIPAUser.get_client", autospec=True, return_value=otp_lookup_client),
            patch("django.contrib.auth.forms.authenticate", autospec=True) as authenticate,
        ):
            authenticate.return_value = None

            form = FreeIPAAuthenticationForm(
                request=request,
                data={
                    "username": "alice",
                    "password": "pw",
                    "otp": "123456",
                },
            )

            self.assertFalse(form.is_valid())
            self.assertEqual(authenticate.call_count, 1)

    def test_otp_retry_does_not_fallback_when_otp_lookup_errors(self) -> None:
        request = RequestFactory().post("/login/")

        with (
            patch(
                "core.forms_auth.FreeIPAUser.get_client",
                autospec=True,
                side_effect=RuntimeError("otp lookup failed"),
            ),
            patch("django.contrib.auth.forms.authenticate", autospec=True) as authenticate,
        ):
            authenticate.return_value = None

            form = FreeIPAAuthenticationForm(
                request=request,
                data={
                    "username": "alice",
                    "password": "pw",
                    "otp": "123456",
                },
            )

            self.assertFalse(form.is_valid())
            self.assertEqual(authenticate.call_count, 1)


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
