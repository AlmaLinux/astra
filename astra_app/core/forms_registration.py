from __future__ import annotations

from django import forms

from core.profanity import validate_no_profanity_or_hate_speech
from core.views_utils import _normalize_str

_USERNAME_RE = r"^[a-z0-9](?:[a-z0-9-]{3,30})[a-z0-9]$"  # length 5..32, no leading/trailing '-'


class RegistrationForm(forms.Form):
    username = forms.RegexField(
        regex=_USERNAME_RE,
        label="Username",
        min_length=5,
        max_length=32,
        required=True,
        help_text='Allowed: a-z, 0-9, and "-" (no leading/trailing dashes).',
    )
    first_name = forms.CharField(label="First name", required=True, max_length=64)
    last_name = forms.CharField(label="Last name", required=True, max_length=64)
    email = forms.EmailField(label="Email address", required=True)

    over_16 = forms.BooleanField(
        label="I am over 16 years old",
        required=True,
        error_messages={"required": "You must be over 16 years old to create an account"},
    )

    invitation_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control")

    def clean_username(self) -> str:
        username = _normalize_str(self.cleaned_data.get("username"))
        if username != username.lower():
            raise forms.ValidationError("Mixed case is not allowed; use lowercase.")
        return validate_no_profanity_or_hate_speech(username, field_label="Username")

    def clean_email(self) -> str:
        email = _normalize_str(self.cleaned_data.get("email")).lower()
        return validate_no_profanity_or_hate_speech(email, field_label="Email address")

    def clean_first_name(self) -> str:
        value = _normalize_str(self.cleaned_data.get("first_name"))
        return validate_no_profanity_or_hate_speech(value, field_label="First name")

    def clean_last_name(self) -> str:
        value = _normalize_str(self.cleaned_data.get("last_name"))
        return validate_no_profanity_or_hate_speech(value, field_label="Last name")

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean()
        for field_name, field_label in (
            ("username", "Username"),
            ("email", "Email address"),
        ):
            raw_value = _normalize_str(self.data.get(field_name))
            if not raw_value:
                continue
            try:
                validate_no_profanity_or_hate_speech(raw_value, field_label=field_label)
            except forms.ValidationError as exc:
                if field_name in self.errors:
                    del self.errors[field_name]
                self.add_error(field_name, exc)
        return cleaned_data


class ResendRegistrationEmailForm(forms.Form):
    username = forms.CharField(widget=forms.HiddenInput, required=True)


class PasswordSetForm(forms.Form):
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput,
        required=True,
        min_length=6,
        max_length=122,
        help_text="Choose a strong password.",
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput,
        required=True,
        min_length=6,
        max_length=122,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get("password")
        pw2 = cleaned.get("password_confirm")
        if pw and pw2 and pw != pw2:
            raise forms.ValidationError("Passwords must match")
        return cleaned
