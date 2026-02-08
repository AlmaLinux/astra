"""Tests for shared FAS field validator functions.

These validators are used by both the admin IPAGroupForm and the frontend
GroupEditForm, so we test the standalone functions directly.
"""

from django import forms
from django.test import TestCase

from core.form_validators import (
    clean_fas_discussion_url_value,
    clean_fas_irc_channels_value,
    clean_fas_mailing_list_value,
    clean_fas_url_value,
)


class CleanFasUrlValueTests(TestCase):
    def test_valid_url_passes(self) -> None:
        result = clean_fas_url_value("https://example.org/group", field_label="FAS URL")
        assert result == "https://example.org/group"

    def test_empty_string_returns_empty(self) -> None:
        assert clean_fas_url_value("", field_label="FAS URL") == ""

    def test_none_returns_empty(self) -> None:
        assert clean_fas_url_value(None, field_label="FAS URL") == ""

    def test_invalid_scheme_raises(self) -> None:
        with self.assertRaises(forms.ValidationError):
            clean_fas_url_value("ftp://example.org", field_label="FAS URL")


class CleanFasDiscussionUrlValueTests(TestCase):
    def test_valid_url_passes(self) -> None:
        result = clean_fas_discussion_url_value("https://discuss.example.org")
        assert result == "https://discuss.example.org"

    def test_empty_returns_empty(self) -> None:
        assert clean_fas_discussion_url_value("") == ""

    def test_invalid_url_raises(self) -> None:
        with self.assertRaises(forms.ValidationError):
            clean_fas_discussion_url_value("not-a-url")


class CleanFasMailingListValueTests(TestCase):
    def test_valid_email_passes(self) -> None:
        result = clean_fas_mailing_list_value("group@lists.example.org")
        assert result == "group@lists.example.org"

    def test_empty_returns_empty(self) -> None:
        assert clean_fas_mailing_list_value("") == ""

    def test_invalid_email_raises(self) -> None:
        with self.assertRaises(forms.ValidationError):
            clean_fas_mailing_list_value("not-an-email")


class CleanFasIrcChannelsValueTests(TestCase):
    def test_single_channel(self) -> None:
        result = clean_fas_irc_channels_value("#channel")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_empty_returns_empty_list(self) -> None:
        assert clean_fas_irc_channels_value("") == []

    def test_multiline_input(self) -> None:
        result = clean_fas_irc_channels_value("#a\n#b\n#c")
        assert isinstance(result, list)
        assert len(result) == 3

    def test_invalid_channel_raises(self) -> None:
        # A channel name exceeding max_item_len should raise
        with self.assertRaises(forms.ValidationError):
            clean_fas_irc_channels_value("x" * 200)
