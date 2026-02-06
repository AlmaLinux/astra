from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from core.forms_registration import RegistrationForm


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