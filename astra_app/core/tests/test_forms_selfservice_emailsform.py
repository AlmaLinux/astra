
from unittest.mock import patch

from django.test import SimpleTestCase

from core.forms_selfservice import EmailsForm


class EmailsFormValidationTests(SimpleTestCase):
    def test_mail_profanity_like_value_is_allowed_when_validation_disabled(self):
        form = EmailsForm(
            data={
                "mail": "shit@example.com",
                "fasRHBZEmail": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["mail"], "shit@example.com")

    def test_bugzilla_email_hate_speech_detector_is_skipped_when_validation_disabled(self):
        with patch("core.profanity._detects_hate_speech", autospec=True, return_value=True) as detect_hate_speech:
            form = EmailsForm(
                data={
                    "mail": "alice@example.com",
                    "fasRHBZEmail": "alice+bug@example.com",
                }
            )

            self.assertTrue(form.is_valid(), form.errors)

        detect_hate_speech.assert_not_called()
