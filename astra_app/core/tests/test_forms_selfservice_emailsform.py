
from unittest.mock import patch

from django.test import SimpleTestCase

from core.forms_selfservice import EmailsForm


class EmailsFormValidationTests(SimpleTestCase):
    def test_mail_rejects_profanity(self):
        form = EmailsForm(
            data={
                "mail": "shit@example.com",
                "fasRHBZEmail": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("mail", form.errors)

    def test_bugzilla_email_rejects_hate_speech(self):
        with patch("core.profanity._detects_hate_speech", autospec=True, return_value=True):
            form = EmailsForm(
                data={
                    "mail": "alice@example.com",
                    "fasRHBZEmail": "alice+bug@example.com",
                }
            )

            self.assertFalse(form.is_valid())
            self.assertIn("fasRHBZEmail", form.errors)