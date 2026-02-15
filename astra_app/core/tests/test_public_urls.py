from django.test import SimpleTestCase, override_settings

from core.public_urls import build_public_absolute_url, normalize_public_base_url


class PublicUrlsTests(SimpleTestCase):
    def test_normalize_public_base_url_contract(self) -> None:
        self.assertEqual(normalize_public_base_url("https://example.com"), "https://example.com")
        self.assertEqual(normalize_public_base_url("https://example.com/"), "https://example.com")
        self.assertEqual(normalize_public_base_url("  https://example.com/  "), "https://example.com")
        self.assertEqual(normalize_public_base_url(""), "")
        self.assertEqual(normalize_public_base_url("   "), "")

    @override_settings(PUBLIC_BASE_URL="https://example.com")
    def test_build_public_absolute_url_joins_normalized_base_and_path(self) -> None:
        self.assertEqual(build_public_absolute_url("/x"), "https://example.com/x")

    @override_settings(PUBLIC_BASE_URL="https://example.com/")
    def test_build_public_absolute_url_accepts_path_without_leading_slash(self) -> None:
        self.assertEqual(build_public_absolute_url("x"), "https://example.com/x")

    @override_settings(PUBLIC_BASE_URL="https://example.com")
    def test_build_public_absolute_url_preserves_query_and_fragment(self) -> None:
        self.assertEqual(build_public_absolute_url("/x?y=1#z"), "https://example.com/x?y=1#z")

    @override_settings(PUBLIC_BASE_URL="")
    def test_build_public_absolute_url_missing_base_raise_mode_raises_value_error(self) -> None:
        with self.assertRaisesMessage(ValueError, "PUBLIC_BASE_URL"):
            build_public_absolute_url("x", on_missing="raise")

    @override_settings(PUBLIC_BASE_URL="")
    def test_build_public_absolute_url_missing_base_relative_mode_returns_relative_path(self) -> None:
        self.assertEqual(build_public_absolute_url("x", on_missing="relative"), "/x")

    @override_settings(PUBLIC_BASE_URL="")
    def test_build_public_absolute_url_missing_base_empty_mode_returns_empty_string(self) -> None:
        self.assertEqual(build_public_absolute_url("x", on_missing="empty"), "")
