
from django import forms
from django.contrib.auth.forms import AuthenticationForm

from core.forms_base import StyledForm, _StyledFormMixin
from core.forms_security import (
    PasswordConfirmationMixin,
    make_otp_field,
    make_password_confirmation_field,
    make_password_field,
)
from core.freeipa.user import FreeIPAUser
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
        username = _normalize_str(self.cleaned_data.get("username"))
        password = (self.cleaned_data.get("password") or "")
        otp = _normalize_str(self.cleaned_data.get("otp"))
        resolved_username = username

        if username and "@" in username:
            resolved_username = ""
            try:
                user = FreeIPAUser.find_by_email(username)
            except Exception:
                user = None

            if user is not None:
                resolved_username = _normalize_str(user.username)
                if resolved_username:
                    self.cleaned_data["username"] = resolved_username

        if not password or not otp:
            return super().clean()

        # Fail closed unless token lookup explicitly proves this account has no OTP tokens.
        self.cleaned_data["password"] = f"{password}{otp}"
        try:
            return super().clean()
        except forms.ValidationError as invalid_login_error:
            if not resolved_username:
                raise invalid_login_error

            try:
                otp_lookup = FreeIPAUser.get_client().otptoken_find(
                    o_ipatokenowner=resolved_username,
                    o_all=True,
                )
            except Exception:
                raise invalid_login_error

            if not isinstance(otp_lookup, dict):
                raise invalid_login_error

            tokens = otp_lookup.get("result")
            if not isinstance(tokens, list) or tokens:
                raise invalid_login_error

            self.cleaned_data["password"] = password
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

