
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core.profanity import (
    _detects_profanity_in_identifier,
    load_custom_profanity_words,
    load_profanity_allowlist_words,
    validate_no_profanity_or_hate_speech,
)


class ProfanityConfigTests(SimpleTestCase):
    def test_load_custom_profanity_words_includes_sample_word(self):
        load_custom_profanity_words.cache_clear()
        words = load_custom_profanity_words()
        self.assertIn("мразь", words)

    @override_settings(VALX_PROFANITY_WORDS_FILE=settings.BASE_DIR / "core" / "data" / "custom_profanity.txt")
    def test_load_custom_profanity_words_uses_configured_path(self):
        load_custom_profanity_words.cache_clear()
        words = load_custom_profanity_words()
        self.assertIn("мразь", words)

    def test_load_profanity_allowlist_words_includes_sample_word(self):
        load_profanity_allowlist_words.cache_clear()
        words = load_profanity_allowlist_words()
        self.assertIn("rumpelein", words)

    @override_settings(
        VALX_PROFANITY_ALLOWLIST_FILE=settings.BASE_DIR / "core" / "data" / "profanity_allowlist.txt"
    )
    def test_load_profanity_allowlist_words_uses_configured_path(self):
        load_profanity_allowlist_words.cache_clear()
        words = load_profanity_allowlist_words()
        self.assertIn("rumpelein", words)


class ProfanityAllowlistValidationTests(SimpleTestCase):
    def test_validate_skips_detection_for_allowlisted_value(self) -> None:
        load_profanity_allowlist_words.cache_clear()
        with (
            patch("core.profanity._detects_profanity", autospec=True) as detect_profanity,
            patch("core.profanity._detects_profanity_in_identifier", autospec=True) as detect_identifier,
            patch("core.profanity._detects_hate_speech", autospec=True) as detect_hate,
        ):
            cleaned = validate_no_profanity_or_hate_speech("Rumpelein", field_label="Username")

        self.assertEqual(cleaned, "Rumpelein")
        detect_profanity.assert_not_called()
        detect_identifier.assert_not_called()
        detect_hate.assert_not_called()

    @override_settings(VALX_PROFANITY_VALIDATION_ENABLED=False)
    def test_validate_skips_all_detection_when_validation_disabled(self) -> None:
        with (
            patch("core.profanity._detects_profanity", autospec=True) as detect_profanity,
            patch("core.profanity._detects_profanity_in_identifier", autospec=True) as detect_identifier,
            patch("core.profanity._detects_hate_speech", autospec=True) as detect_hate,
        ):
            cleaned = validate_no_profanity_or_hate_speech("procomputers", field_label="Username")

        self.assertEqual(cleaned, "procomputers")
        detect_profanity.assert_not_called()
        detect_identifier.assert_not_called()
        detect_hate.assert_not_called()


class ProfanityIdentifierDetectionTests(SimpleTestCase):
    def test_identifier_detection_does_not_match_across_token_boundaries(self) -> None:
        with patch("core.profanity._profanity_keywords", return_value=["kusi"]):
            self.assertFalse(_detects_profanity_in_identifier("markus@itworxx.de"))

    def test_identifier_detection_matches_single_character_token_obfuscation(self) -> None:
        with patch("core.profanity._profanity_keywords", return_value=["fuck"]):
            self.assertTrue(_detects_profanity_in_identifier("f.u.c.k"))