"""Shared form validation helpers.

Consolidates URL validation, list-field splitting, and password-confirm
logic that was previously duplicated across forms_groups.py,
forms_selfservice.py, forms_auth.py, forms_registration.py, and admin.py.
"""


from urllib.parse import urlparse

from django import forms

from core.ipa_user_attrs import _split_list_field


def validate_http_url(value: str, *, field_label: str) -> str:
    """Validate a single HTTP/HTTPS URL (max 255 chars, non-empty host).

    Returns the stripped value, or "" if blank.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) > 255:
        raise forms.ValidationError(f"Invalid {field_label}: must be at most 255 characters")

    parsed = urlparse(v)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise forms.ValidationError(f"Invalid {field_label}: URL must start with http:// or https://")
    if not parsed.netloc:
        raise forms.ValidationError(f"Invalid {field_label}: empty host name")
    return v


def validate_http_urls(value: str, *, field_label: str) -> str:
    """Validate a newline/comma-separated list of HTTP/HTTPS URLs.

    Each URL is validated individually via validate_http_url.
    Returns normalized newline-separated output.
    """
    urls = [u.strip() for u in _split_list_field(value)]
    normalized: list[str] = []
    for u in urls:
        if not u:
            continue
        # validate_http_url already checks length, scheme, and netloc
        validated = validate_http_url(u, field_label=field_label)
        if validated:
            normalized.append(validated)
    return "\n".join(normalized)


def clean_password_confirm(
    cleaned_data: dict[str, object],
    *,
    password_field: str = "new_password",
    confirm_field: str = "confirm_new_password",
    error_message: str = "Passwords must match",
) -> None:
    """Raise ValidationError if password and confirm fields don't match.

    Mutates nothing; raises forms.ValidationError on mismatch.
    """
    pw = cleaned_data.get(password_field)
    pw2 = cleaned_data.get(confirm_field)
    if pw and pw2 and pw != pw2:
        raise forms.ValidationError(error_message)


# --- Shared FAS group field validators ---
#
# Used by both admin IPAGroupForm and frontend GroupEditForm so that FAS
# attribute validation is defined in exactly one place.

def clean_fas_url_value(value: str | None, *, field_label: str = "FAS URL") -> str:
    """Validate and return a FAS URL value."""
    return validate_http_url(str(value or "").strip(), field_label=field_label)


def clean_fas_discussion_url_value(value: str | None, *, field_label: str = "FAS Discussion URL") -> str:
    """Validate and return a FAS discussion URL value."""
    return validate_http_url(str(value or "").strip(), field_label=field_label)


def clean_fas_mailing_list_value(value: str | None) -> str:
    """Validate and return a FAS mailing list email value."""
    v = str(value or "").strip()
    if not v:
        return ""
    return forms.EmailField(required=False).clean(v)


def clean_fas_irc_channels_value(value: str | None) -> list[str]:
    """Validate and return a list of normalized FAS IRC/chat channel names.

    Always returns a list of strings (one per channel). Both the admin form
    and the frontend form should use this canonical return type; callers that
    need a newline-joined string can join the result.
    """
    from core.chatnicknames import normalize_chat_channels_text

    raw = str(value or "")
    try:
        normalized = normalize_chat_channels_text(raw, max_item_len=64)
    except ValueError as exc:
        raise forms.ValidationError(f"Invalid FAS IRC Channels: {exc}") from exc

    return [line for line in normalized.splitlines() if line.strip()]
