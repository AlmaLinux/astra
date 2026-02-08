
from django.test import SimpleTestCase

from core.forms_selfservice import ProfileForm, _get_country_choices


class ProfileFormValidationTests(SimpleTestCase):
    def test_country_code_choices_include_placeholder_and_us(self):
        choices = _get_country_choices()
        self.assertGreater(len(choices), 1)
        self.assertEqual(choices[0], ("", "Select a country..."))
        self.assertTrue(any(code == "US" and label.endswith(" - US") for code, label in choices))

    def test_country_code_accepts_valid_alpha2_choice(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["country_code"], "US")

    def test_country_code_rejects_invalid_alpha2(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "ZZZ",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("country_code", form.errors)

    def test_country_code_is_required(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("country_code", form.errors)

    def test_givenname_rejects_profanity(self):
        form = ProfileForm(
            data={
                "givenname": "shit",
                "sn": "User",
                "country_code": "US",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("givenname", form.errors)

    def test_sn_rejects_profanity(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "shit",
                "country_code": "US",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("sn", form.errors)

    def test_github_username_strips_at_and_validates(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasGitHubUsername": "@octocat",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fasGitHubUsername"], "octocat")

    def test_github_username_rejects_invalid(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasGitHubUsername": "-bad-",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("fasGitHubUsername", form.errors)

    def test_gitlab_username_strips_at_and_validates(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasGitLabUsername": "@good.name",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fasGitLabUsername"], "good.name")

    def test_chat_nick_accepts_matrix_url_forms_and_keeps_scheme(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasIRCNick": "matrix://matrix.example/alice\nmatrix:/bob",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Noggin stores chat nicks as URLs that include the scheme (irc/matrix).
        # This ensures we can render correct links later.
        cleaned = form.cleaned_data["fasIRCNick"].splitlines()
        self.assertIn("matrix://matrix.example/alice", cleaned)
        self.assertIn("matrix:/bob", cleaned)

    def test_chat_nick_accepts_mattermost_url_forms_and_keeps_scheme(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasIRCNick": "mattermost://chat.almalinux.org/almalinux/alice\nmattermost:/bob",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        cleaned = form.cleaned_data["fasIRCNick"].splitlines()
        self.assertIn("mattermost://chat.almalinux.org/almalinux/alice", cleaned)
        self.assertIn("mattermost:/bob", cleaned)

    def test_chat_nick_rejects_mattermost_custom_server_without_team(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasIRCNick": "mattermost://chat.example.org/alice",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("fasIRCNick", form.errors)
        self.assertIn("team", " ".join(form.errors["fasIRCNick"]).lower())

    def test_timezone_accepts_valid_iana_timezone(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasTimezone": "UTC",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fasTimezone"], "UTC")

    def test_timezone_rejects_invalid_timezone(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasTimezone": "Not/AZone",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("fasTimezone", form.errors)

    def test_website_url_requires_http_or_https(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasWebsiteUrl": "ftp://example.org",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("fasWebsiteUrl", form.errors)

    def test_website_url_accepts_multiple_lines_and_commas(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasWebsiteUrl": "https://example.org, https://example.com\nhttps://example.net",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_irc_nick_accepts_nick_and_nick_server(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasIRCNick": "nick\nnick:irc.example.org",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_irc_nick_accepts_irc_url_forms(self):
        form = ProfileForm(
            data={
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasIRCNick": "irc://irc.example.org/nick",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fasIRCNick"].strip(), "irc://irc.example.org/nick")
