from typing import override

from django import forms

from core.form_validators import clean_password_confirm


def make_password_field(
    *,
    label: str,
    required: bool = True,
    min_length: int | None = None,
    max_length: int | None = None,
    help_text: str = "",
    autocomplete: str | None = None,
) -> forms.CharField:
    widget = forms.PasswordInput()
    if autocomplete:
        widget.attrs["autocomplete"] = autocomplete

    return forms.CharField(
        label=label,
        widget=widget,
        required=required,
        min_length=min_length,
        max_length=max_length,
        help_text=help_text,
    )


def make_password_confirmation_field(
    *,
    label: str,
    required: bool = True,
    min_length: int | None = None,
    max_length: int | None = None,
    help_text: str = "",
    autocomplete: str | None = None,
) -> forms.CharField:
    return make_password_field(
        label=label,
        required=required,
        min_length=min_length,
        max_length=max_length,
        help_text=help_text,
        autocomplete=autocomplete,
    )


def make_otp_field(
    *,
    label: str = "One-Time Password",
    required: bool = False,
    help_text: str = "",
    autocomplete: str | None = None,
) -> forms.CharField:
    widget = forms.TextInput()
    if autocomplete:
        widget.attrs["autocomplete"] = autocomplete

    return forms.CharField(
        label=label,
        required=required,
        help_text=help_text,
        widget=widget,
    )


class PasswordConfirmationMixin(forms.Form):
    password_field_name: str = "new_password"
    confirm_password_field_name: str = "confirm_new_password"
    password_mismatch_error_message: str = "Passwords must match"

    @override
    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        clean_password_confirm(
            cleaned,
            password_field=self.password_field_name,
            confirm_field=self.confirm_password_field_name,
            error_message=self.password_mismatch_error_message,
        )
        return cleaned
