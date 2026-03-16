from __future__ import annotations

from dataclasses import dataclass

from django import forms

from core.chatnicknames import normalize_chat_nicknames_text
from core.form_validators import validate_http_urls
from core.forms_selfservice import (
    _GITHUB_USERNAME_RE,
    _GITLAB_USERNAME_RE,
    ProfileForm,
    _get_timezones,
    _is_valid_locale_code,
    _normalize_profile_handle,
    normalize_locale_tag,
)
from core.ipa_user_attrs import _data_get, _first, _value_to_text
from core.profanity import validate_no_profanity_or_hate_speech
from core.views_utils import _normalize_str


@dataclass(frozen=True, slots=True)
class AuditFinding:
    username: str
    attribute: str
    issue: str
    value: str
    message: str
    suggested: str | None = None


def audit_fas_user_attributes(
    *,
    username: str,
    user_data: dict[str, object],
    include_non_canonical: bool = False,
) -> list[AuditFinding]:
    """Audit a FreeIPA user's FAS-related attribute values.

    - "invalid" findings indicate values that would fail the same validation
      used by the self-service profile editor.
    - "non_canonical" findings indicate values that are accepted but would be
      normalized to a different stored representation.

    The caller controls whether to include non-canonical findings.
    """

    findings: list[AuditFinding] = []

    def _add_invalid(attr: str, value: str, msg: str) -> None:
        findings.append(
            AuditFinding(
                username=username,
                attribute=attr,
                issue="invalid",
                value=value,
                message=msg,
            )
        )

    def _add_non_canonical(attr: str, value: str, suggested: str, msg: str) -> None:
        if not include_non_canonical:
            return
        findings.append(
            AuditFinding(
                username=username,
                attribute=attr,
                issue="non_canonical",
                value=value,
                suggested=suggested,
                message=msg,
            )
        )

    # --- fasLocale ---
    raw_locale = _normalize_str(_first(user_data, "fasLocale", ""))
    normalized_locale = normalize_locale_tag(raw_locale)
    if raw_locale and normalized_locale and raw_locale != normalized_locale:
        _add_non_canonical(
            "fasLocale",
            raw_locale,
            normalized_locale,
            "Locale would be normalized",
        )
    if len(normalized_locale) > 64:
        _add_invalid("fasLocale", raw_locale, "Locale must be at most 64 characters")
    elif normalized_locale and not _is_valid_locale_code(normalized_locale):
        _add_invalid("fasLocale", raw_locale, "Locale must be a valid locale short-code")

    # --- fasTimezone ---
    raw_timezone = _normalize_str(_first(user_data, "fasTimezone", ""))
    if len(raw_timezone) > 64:
        _add_invalid("fasTimezone", raw_timezone, "Timezone must be at most 64 characters")
    elif raw_timezone and raw_timezone not in _get_timezones():
        _add_invalid("fasTimezone", raw_timezone, "Timezone must be a valid IANA timezone")

    # --- fasGitHubUsername ---
    raw_github = _normalize_str(_first(user_data, "fasGitHubUsername", ""))
    normalized_github = _normalize_profile_handle(raw_github, expected_host="github.com")
    if raw_github and normalized_github and raw_github != normalized_github:
        _add_non_canonical(
            "fasGitHubUsername",
            raw_github,
            normalized_github,
            "GitHub username would be normalized",
        )
    if normalized_github and not _GITHUB_USERNAME_RE.match(normalized_github):
        _add_invalid("fasGitHubUsername", raw_github, "GitHub username is not valid")

    # --- fasGitLabUsername ---
    raw_gitlab = _normalize_str(_first(user_data, "fasGitLabUsername", ""))
    normalized_gitlab = _normalize_profile_handle(raw_gitlab, expected_host="gitlab.com")
    if raw_gitlab and normalized_gitlab and raw_gitlab != normalized_gitlab:
        _add_non_canonical(
            "fasGitLabUsername",
            raw_gitlab,
            normalized_gitlab,
            "GitLab username would be normalized",
        )
    if normalized_gitlab and not _GITLAB_USERNAME_RE.match(normalized_gitlab):
        _add_invalid("fasGitLabUsername", raw_gitlab, "GitLab username is not valid")

    # --- fasWebsiteUrl / fasRssUrl ---
    raw_website = _value_to_text(_data_get(user_data, "fasWebsiteUrl", []))
    try:
        normalized_website = validate_http_urls(raw_website, field_label="Website URL")
        if raw_website and raw_website != normalized_website:
            _add_non_canonical(
                "fasWebsiteUrl",
                raw_website,
                normalized_website,
                "Website URL list would be normalized",
            )
    except forms.ValidationError as exc:
        _add_invalid("fasWebsiteUrl", raw_website, str(exc))

    raw_rss = _value_to_text(_data_get(user_data, "fasRssUrl", []))
    try:
        normalized_rss = validate_http_urls(raw_rss, field_label="RSS URL")
        if raw_rss and raw_rss != normalized_rss:
            _add_non_canonical(
                "fasRssUrl",
                raw_rss,
                normalized_rss,
                "RSS URL list would be normalized",
            )
    except forms.ValidationError as exc:
        _add_invalid("fasRssUrl", raw_rss, str(exc))

    # --- fasIRCNick ---
    raw_chat = _value_to_text(_data_get(user_data, "fasIRCNick", []))
    try:
        normalized_chat = normalize_chat_nicknames_text(raw_chat, max_item_len=64)
        if raw_chat and raw_chat != normalized_chat:
            _add_non_canonical(
                "fasIRCNick",
                raw_chat,
                normalized_chat,
                "Chat nicknames would be normalized",
            )
    except ValueError as exc:
        _add_invalid("fasIRCNick", raw_chat, str(exc))

    # --- fasPronoun ---
    raw_pronoun = _value_to_text(_data_get(user_data, "fasPronoun", []))
    try:
        normalized_pronoun = ProfileForm._validate_multivalued_maxlen(
            raw_pronoun,
            field_label="Pronouns",
            maxlen=64,
        )
        if raw_pronoun and raw_pronoun != normalized_pronoun:
            _add_non_canonical(
                "fasPronoun",
                raw_pronoun,
                normalized_pronoun,
                "Pronouns would be normalized",
            )
    except forms.ValidationError as exc:
        _add_invalid("fasPronoun", raw_pronoun, str(exc))

    # --- fasGPGKeyId ---
    raw_gpg = _value_to_text(_data_get(user_data, "fasGPGKeyId", []))
    try:
        normalized_gpg = ProfileForm._validate_gpg_key_ids(raw_gpg)
        if raw_gpg and raw_gpg != normalized_gpg:
            _add_non_canonical(
                "fasGPGKeyId",
                raw_gpg,
                normalized_gpg,
                "GPG key IDs would be normalized",
            )
    except forms.ValidationError as exc:
        _add_invalid("fasGPGKeyId", raw_gpg, str(exc))

    # --- fasRHBZEmail ---
    raw_rhbz = _normalize_str(_first(user_data, "fasRHBZEmail", "")).lower()
    if raw_rhbz:
        try:
            forms.EmailField(required=False, max_length=255).clean(raw_rhbz)
            validate_no_profanity_or_hate_speech(raw_rhbz, field_label="Bugzilla email")
        except forms.ValidationError as exc:
            _add_invalid("fasRHBZEmail", raw_rhbz, str(exc))

    return findings
