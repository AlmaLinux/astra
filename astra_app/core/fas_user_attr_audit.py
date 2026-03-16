from __future__ import annotations

import datetime
import re
import zoneinfo
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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

    def _add_invalid(attr: str, value: str, msg: str, *, suggested: str | None = None) -> None:
        findings.append(
            AuditFinding(
                username=username,
                attribute=attr,
                issue="invalid",
                value=value,
                message=msg,
                suggested=suggested,
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
    elif raw_timezone:
        if raw_timezone not in _get_timezones():
            suggested = _canonical_iana_timezone_name(raw_timezone) or _suggest_iana_timezone(raw_timezone)
            _add_invalid(
                "fasTimezone",
                raw_timezone,
                "Timezone must be a valid IANA timezone",
                suggested=suggested,
            )
        else:
            canonical = _canonical_iana_timezone_name(raw_timezone)
            if canonical and canonical != raw_timezone:
                _add_non_canonical(
                    "fasTimezone",
                    raw_timezone,
                    canonical,
                    "Timezone is a deprecated alias; prefer the canonical IANA name",
                )

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


_UTC_OFFSET_RE = re.compile(r"^(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$")


@lru_cache(maxsize=1)
def _available_timezones_lower_map() -> dict[str, str]:
    # Map case-insensitive timezone names to their canonical form.
    return {tz.lower(): tz for tz in zoneinfo.available_timezones()}


@lru_cache(maxsize=1)
def _available_timezones_last_segment_map() -> dict[str, list[str]]:
    # Map "Zurich" -> ["Europe/Zurich"] etc.
    out: dict[str, list[str]] = {}
    for tz in zoneinfo.available_timezones():
        last = tz.rsplit("/", 1)[-1]
        out.setdefault(last.lower(), []).append(tz)
    for key, values in out.items():
        values.sort()
        out[key] = values
    return out


@lru_cache(maxsize=1)
def _tz_abbrev_candidates() -> dict[str, list[str]]:
    """Build a best-effort mapping from abbreviation to candidate IANA TZs.

    Abbreviations are inherently ambiguous; this only provides suggestions.
    """

    # Use two dates to cover DST and standard time abbreviations.
    dt_winter = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.UTC)
    dt_summer = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)

    candidates: dict[str, set[str]] = {}
    for tz_name in zoneinfo.available_timezones():
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            continue

        for dt in (dt_winter, dt_summer):
            try:
                abbr = dt.astimezone(tz).tzname() or ""
            except Exception:
                continue
            abbr = abbr.strip().upper()
            if not abbr:
                continue
            candidates.setdefault(abbr, set()).add(tz_name)

    out: dict[str, list[str]] = {k: sorted(v) for k, v in candidates.items()}
    return out


def _format_candidates(label: str, tzs: list[str], *, limit: int = 10) -> str:
    if not tzs:
        return ""
    shown = tzs[:limit]
    suffix = "" if len(tzs) <= limit else f" (+{len(tzs) - limit} more)"
    return f"{label}: {', '.join(shown)}{suffix}"


def _suggest_iana_timezone(raw_value: str) -> str | None:
    value = _normalize_str(raw_value)
    if not value:
        return None

    canonical = _canonical_iana_timezone_name(value)
    if canonical:
        return canonical

    lowered = value.lower()
    lower_map = _available_timezones_lower_map()

    # Case-insensitive exact match.
    if lowered in lower_map:
        return lower_map[lowered]

    # Common whitespace/underscore normalization.
    squashed = re.sub(r"\s+", "_", value.strip()).lower()
    if squashed in lower_map:
        return lower_map[squashed]

    # Match by last segment (e.g. "Zurich" -> "Europe/Zurich").
    # For legacy Link-style inputs like "US/Eastern", this checks the final
    # component ("Eastern") rather than the whole string.
    last_segment_key = lowered.rsplit("/", 1)[-1]
    last_segment_matches = _available_timezones_last_segment_map().get(last_segment_key, [])
    if len(last_segment_matches) == 1:
        return last_segment_matches[0]
    if len(last_segment_matches) > 1:
        return _format_candidates("Candidates", last_segment_matches)

    # Abbreviation match (e.g. "EST").
    if value.isalpha() and 2 <= len(value) <= 5:
        abbr = value.upper()
        matches = _tz_abbrev_candidates().get(abbr, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return _format_candidates("Candidates", matches)

    # UTC/GMT offset match (e.g. "UTC+2", "GMT-05:00").
    m = _UTC_OFFSET_RE.match(value.upper().replace(" ", ""))
    if m:
        sign, hh, mm = m.group(1), int(m.group(2)), int(m.group(3) or "0")
        if 0 <= hh <= 14 and mm in {0, 15, 30, 45}:
            if mm == 0:
                # Etc/GMT signs are reversed (Etc/GMT+5 == UTC-5)
                etc_sign = "+" if sign == "-" else "-"
                etc_name = f"Etc/GMT{etc_sign}{hh}" if hh else "Etc/UTC"
                if etc_name.lower() in lower_map:
                    return lower_map[etc_name.lower()]

            # For non-whole-hour offsets, suggest candidate zones by current UTC offset.
            target_minutes = (hh * 60 + mm) * (1 if sign == "+" else -1)
            dt = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.UTC)
            offset_matches: list[str] = []
            for tz_name in zoneinfo.available_timezones():
                try:
                    tz = zoneinfo.ZoneInfo(tz_name)
                    off = dt.astimezone(tz).utcoffset()
                except Exception:
                    continue
                if off is None:
                    continue
                if int(off.total_seconds() // 60) == target_minutes:
                    offset_matches.append(tz_name)
            offset_matches.sort()
            if len(offset_matches) == 1:
                return offset_matches[0]
            if len(offset_matches) > 1:
                return _format_candidates("Candidates", offset_matches)

    return None


@lru_cache(maxsize=1)
def _tzdb_backward_link_map() -> dict[str, str]:
    """Return a mapping of tzdb Link aliases to canonical zone names.

    This uses the tzdb `backward` file when present. On most Linux systems,
    these Link names are also implemented as filesystem symlinks under
    `/usr/share/zoneinfo`, but the `backward` file provides a stable, explicit
    mapping when available.
    """

    mapping: dict[str, str] = {}
    for root in getattr(zoneinfo, "TZPATH", ()):
        path = Path(root) / "backward"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3 or parts[0] != "Link":
                continue
            target, link_name = parts[1], parts[2]
            if target and link_name:
                mapping[link_name.lower()] = target

    return mapping


def _canonical_iana_timezone_name(raw_value: str) -> str | None:
    """Return the canonical IANA timezone for a deprecated/alias name.

    If `raw_value` is not a known Link name, returns None.
    """

    value = _normalize_str(raw_value)
    if not value:
        return None

    # Prefer explicit tzdb link metadata when available.
    link_map = _tzdb_backward_link_map()
    target = link_map.get(value.lower())
    if target and target != value:
        return target

    # Fall back to filesystem symlink resolution (common on Linux).
    for root in getattr(zoneinfo, "TZPATH", ()):
        root_path = Path(root)
        candidate = root_path / value
        if not candidate.exists():
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        try:
            rel = resolved.relative_to(root_path)
        except ValueError:
            continue

        rel_parts = rel.parts
        if rel_parts and rel_parts[0] in {"posix", "right"}:
            rel = Path(*rel_parts[1:])

        canonical = rel.as_posix()
        if canonical and canonical != value:
            return canonical

    return None
