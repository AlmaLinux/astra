
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core.profanity import load_custom_profanity_words


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