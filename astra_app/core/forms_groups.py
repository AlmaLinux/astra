
from django import forms

from core.form_validators import (
    clean_fas_discussion_url_value,
    clean_fas_irc_channels_value,
    clean_fas_mailing_list_value,
    clean_fas_url_value,
)
from core.forms_base import StyledForm


class GroupEditForm(StyledForm):
    description = forms.CharField(
        required=False,
        label="Description",
        max_length=255,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "spellcheck": "true"}),
    )
    fas_url = forms.CharField(
        required=False,
        label="URL",
        max_length=255,
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://…"}),
    )
    fas_mailing_list = forms.CharField(
        required=False,
        label="Mailing list",
        max_length=255,
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "group@lists.example.org"}),
    )
    fas_discussion_url = forms.CharField(
        required=False,
        label="Discussion URL",
        max_length=255,
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://…"}),
    )
    fas_irc_channels = forms.CharField(
        required=False,
        label="Chat channels",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text=(
            "One per line (or comma-separated). "
            "Use protocol-aware channel formats: "
            "~channel or ~channel:server:team (Mattermost); "
            "#channel or #channel:server (IRC); "
            "matrix:/#channel or matrix://server/#channel (Matrix)."
        ),
    )

    def clean_description(self) -> str:
        return str(self.cleaned_data.get("description") or "").strip()

    def clean_fas_url(self) -> str:
        return clean_fas_url_value(self.cleaned_data.get("fas_url"), field_label="URL")

    def clean_fas_discussion_url(self) -> str:
        return clean_fas_discussion_url_value(self.cleaned_data.get("fas_discussion_url"), field_label="Discussion URL")

    def clean_fas_mailing_list(self) -> str:
        return clean_fas_mailing_list_value(self.cleaned_data.get("fas_mailing_list"))

    def clean_fas_irc_channels(self) -> list[str]:
        return clean_fas_irc_channels_value(self.cleaned_data.get("fas_irc_channels"))
