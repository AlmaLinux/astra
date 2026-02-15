
from django import forms
from django.contrib.auth.forms import AuthenticationForm

from core.forms_base import StyledForm, _StyledFormMixin
from core.forms_security import (
    PasswordConfirmationMixin,
    make_otp_field,
    make_password_confirmation_field,
    make_password_field,
)
from core.views_utils import _normalize_str


class FreeIPAAuthenticationForm(_StyledFormMixin, AuthenticationForm):
    """AuthenticationForm with a separate OTP field.

    Noggin-style behavior: if OTP is provided, append it to the password before
    calling Django's authenticate().
    """

    otp = make_otp_field()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_css_classes()

    def clean(self):
        # Ensure the OTP is applied before AuthenticationForm runs authenticate().
        password = (self.cleaned_data.get("password") or "")
        otp = _normalize_str(self.cleaned_data.get("otp"))
        if password and otp:
            self.cleaned_data["password"] = f"{password}{otp}"
        return super().clean()


class ExpiredPasswordChangeForm(PasswordConfirmationMixin, StyledForm):
    username = forms.CharField(label="Username", required=True)
    current_password = make_password_field(label="Current Password")
    otp = make_otp_field()
    new_password = make_password_field(label="New Password")
    confirm_new_password = make_password_confirmation_field(label="Confirm New Password")


class SyncTokenForm(StyledForm):
    """Noggin-style OTP token sync form.

    Used when a user's token has drifted and they can no longer log in.
    This posts to FreeIPA's /ipa/session/sync_token endpoint.
    """

    username = forms.CharField(label="Username", required=True)
    password = forms.CharField(label="Password", widget=forms.PasswordInput, required=True)
    first_code = forms.CharField(label="First OTP", required=True)
    second_code = forms.CharField(label="Second OTP", required=True)
    token = forms.CharField(label="Token ID", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_code"].widget.attrs.setdefault("autocomplete", "off")
        self.fields["second_code"].widget.attrs.setdefault("autocomplete", "off")
        self.fields["token"].help_text = "Optional. Leave empty to sync the default token."


class PasswordResetRequestForm(StyledForm):
    username_or_email = forms.CharField(
        label="Username or email",
        required=True,
        max_length=255,
    )

    def clean_username_or_email(self) -> str:
        return _normalize_str(self.cleaned_data.get("username_or_email"))


class PasswordResetSetForm(PasswordConfirmationMixin, StyledForm):
    password_field_name = "password"
    confirm_password_field_name = "password_confirm"

    password = make_password_field(
        label="New password",
        min_length=6,
        max_length=122,
    )
    password_confirm = make_password_confirmation_field(
        label="Confirm new password",
        min_length=6,
        max_length=122,
    )
    otp = make_otp_field(
        autocomplete="off",
    )

    def __init__(self, *args, require_otp: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if require_otp:
            self.fields["otp"].required = True
            self.fields["otp"].help_text = "Required for accounts with two-factor authentication enabled."
        else:
            self.fields["otp"].help_text = "Only required if your account has two-factor authentication enabled."

