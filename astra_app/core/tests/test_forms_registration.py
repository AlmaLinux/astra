
from unittest.mock import patch

from django.test import SimpleTestCase

from core.forms_registration import PasswordSetForm, RegistrationForm
from core.forms_security import PasswordConfirmationMixin, make_password_confirmation_field, make_password_field


class RegistrationFormValidationTests(SimpleTestCase):
    def test_username_rejects_too_short_invalid_value_when_profanity_validation_disabled(self):
        form = RegistrationForm(
            data={
                "username": "shit",
                "first_name": "Alice",
                "last_name": "User",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_username_non_ascii_uses_username_validation_when_profanity_validation_disabled(self):
        form = RegistrationForm(
            data={
                "username": "мразь",
                "first_name": "Alice",
                "last_name": "User",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertFalse(any("disallowed language" in message.lower() for message in form.errors["username"]))

    def test_username_profanity_like_value_is_allowed_when_validation_disabled(self):
        form = RegistrationForm(
            data={
                "username": "fuckputin",
                "first_name": "Alice",
                "last_name": "User",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["username"], "fuckputin")

    def test_first_name_profanity_like_value_is_allowed_when_validation_disabled(self):
        form = RegistrationForm(
            data={
                "username": "alice",
                "first_name": "shit",
                "last_name": "User",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["first_name"], "shit")

    def test_last_name_profanity_like_value_is_allowed_when_validation_disabled(self):
        form = RegistrationForm(
            data={
                "username": "alice",
                "first_name": "Alice",
                "last_name": "shit",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["last_name"], "shit")

    def test_email_profanity_like_value_is_allowed_when_validation_disabled(self):
        form = RegistrationForm(
            data={
                "username": "alice",
                "first_name": "Alice",
                "last_name": "User",
                "email": "shit@example.com",
                "over_16": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "shit@example.com")

    def test_username_hate_speech_detector_is_skipped_when_validation_disabled(self):
        with patch("core.profanity._detects_hate_speech", autospec=True, return_value=True) as detect_hate_speech:
            form = RegistrationForm(
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                }
            )

            self.assertTrue(form.is_valid(), form.errors)

        detect_hate_speech.assert_not_called()


class PasswordSetFormConsolidationTests(SimpleTestCase):
    def test_password_set_form_uses_shared_password_confirmation_mixin(self) -> None:
        self.assertTrue(issubclass(PasswordSetForm, PasswordConfirmationMixin))

    def test_password_set_form_uses_shared_password_field_definitions(self) -> None:
        form = PasswordSetForm()
        expected_password = make_password_field(
            label="Password",
            min_length=6,
            max_length=122,
            help_text="Choose a strong password.",
        )
        expected_confirm = make_password_confirmation_field(
            label="Confirm password",
            min_length=6,
            max_length=122,
        )

        self.assertEqual(form.fields["password"].label, expected_password.label)
        self.assertEqual(form.fields["password"].help_text, expected_password.help_text)
        self.assertEqual(form.fields["password"].min_length, expected_password.min_length)
        self.assertEqual(form.fields["password"].max_length, expected_password.max_length)

        self.assertEqual(form.fields["password_confirm"].label, expected_confirm.label)
        self.assertEqual(form.fields["password_confirm"].help_text, expected_confirm.help_text)
        self.assertEqual(form.fields["password_confirm"].min_length, expected_confirm.min_length)
        self.assertEqual(form.fields["password_confirm"].max_length, expected_confirm.max_length)