
from unittest.mock import patch

from django.test import SimpleTestCase

from core.forms_registration import PasswordSetForm, RegistrationForm
from core.forms_security import PasswordConfirmationMixin, make_password_confirmation_field, make_password_field


class RegistrationFormValidationTests(SimpleTestCase):
    def test_username_rejects_profanity(self):
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

    def test_username_profanity_shows_specific_message_for_non_ascii(self):
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
        self.assertTrue(
            any("disallowed language" in message.lower() for message in form.errors["username"])
        )

    def test_username_rejects_profanity_substring(self):
        form = RegistrationForm(
            data={
                "username": "fuckputin",
                "first_name": "Alice",
                "last_name": "User",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_first_name_rejects_profanity(self):
        form = RegistrationForm(
            data={
                "username": "alice",
                "first_name": "shit",
                "last_name": "User",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("first_name", form.errors)

    def test_last_name_rejects_profanity(self):
        form = RegistrationForm(
            data={
                "username": "alice",
                "first_name": "Alice",
                "last_name": "shit",
                "email": "alice@example.com",
                "over_16": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("last_name", form.errors)

    def test_email_rejects_profanity(self):
        form = RegistrationForm(
            data={
                "username": "alice",
                "first_name": "Alice",
                "last_name": "User",
                "email": "shit@example.com",
                "over_16": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_username_rejects_hate_speech(self):
        with patch("core.profanity._detects_hate_speech", autospec=True, return_value=True):
            form = RegistrationForm(
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                }
            )

            self.assertFalse(form.is_valid())
            self.assertIn("username", form.errors)


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