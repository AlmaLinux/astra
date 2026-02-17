
from django import forms

from core.forms_base import StyledForm
from core.forms_security import PasswordConfirmationMixin, make_password_confirmation_field, make_password_field
from core.profanity import validate_no_profanity_or_hate_speech
from core.views_utils import _normalize_str

_USERNAME_RE = r"^[a-z0-9](?:[a-z0-9-]{3,30})[a-z0-9]$"  # length 5..32, no leading/trailing '-'


class RegistrationForm(StyledForm):
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


class ResendRegistrationEmailForm(StyledForm):
    username = forms.CharField(widget=forms.HiddenInput, required=True)


class PasswordSetForm(PasswordConfirmationMixin, StyledForm):
    password_field_name = "password"
    confirm_password_field_name = "password_confirm"

    password = make_password_field(
        label="Password",
        min_length=6,
        max_length=122,
        help_text="Choose a strong password.",
    )
    password_confirm = make_password_confirmation_field(
        label="Confirm password",
        min_length=6,
        max_length=122,
    )
