import csv
import datetime
import io
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

from dateutil import parser
from django import forms
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from tablib import Dataset

from core.views_utils import _normalize_str

AUTO_DETECT_CHOICE: tuple[str, str] = ("", "Auto-detect")


def build_csv_header_choices(headers: Sequence[str]) -> list[tuple[str, str]]:
    return [AUTO_DETECT_CHOICE, *[(header, header) for header in headers]]


def set_form_column_field_choices(
    *,
    form: forms.Form,
    field_names: Sequence[str],
    headers: Sequence[str],
) -> None:
    choices = build_csv_header_choices(headers)
    for field_name in field_names:
        if field_name in form.fields:
            form.fields[field_name].choices = choices


def norm_csv_header(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def resolve_column_header(
    field_name: str,
    headers: Sequence[str],
    header_by_norm: Mapping[str, str],
    column_overrides: Mapping[str, str],
    *fallback_norms: str,
) -> str | None:
    override = _normalize_str(column_overrides.get(field_name, ""))
    if override:
        if override in headers:
            return override

        override_norm = norm_csv_header(override)
        resolved_override = header_by_norm.get(override_norm)
        if resolved_override:
            return resolved_override

        raise ValueError(f"Column '{override}' not found in CSV headers")

    for fallback in fallback_norms:
        fallback_norm = norm_csv_header(fallback)
        resolved = header_by_norm.get(fallback_norm)
        if resolved:
            return resolved
    return None


def attach_unmatched_csv_to_result(
    result: Any,
    dataset: Dataset,
    cache_key_prefix: str,
    reverse_url_name: str,
) -> None:
    token = secrets.token_urlsafe(16)
    cache_key = f"{cache_key_prefix}:{token}"
    csv_content = dataset.export("csv")
    cache.set(cache_key, csv_content, timeout=60 * 60)

    download_url = reverse(reverse_url_name, kwargs={"token": token})
    # `Result` is a third-party import-export type with no extension hook;
    # dynamic attributes are used as a lightweight duck-typed contract.
    setattr(result, "unmatched_csv_content", csv_content)
    setattr(result, "unmatched_download_url", download_url)


def sanitize_csv_cell(value: str) -> str:
    """Prefix formula-starting characters to prevent spreadsheet formula injection."""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return value


def normalize_csv_email(value: object) -> str:
    return _normalize_str(value).lower()


def normalize_csv_name(value: object) -> str:
    raw = _normalize_str(value).lower()
    return "".join(ch for ch in raw if ch.isalnum())


def parse_csv_bool(value: object) -> bool:
    normalized = _normalize_str(value).lower()
    if not normalized:
        return False
    return normalized in {"1", "y", "yes", "true", "t", "active", "activemember", "active member"}


def parse_csv_date(value: object) -> datetime.date | None:
    raw = _normalize_str(value)
    if not raw:
        return None

    try:
        parsed = parser.parse(raw, dayfirst=False, yearfirst=False)
    except (parser.ParserError, TypeError, ValueError, OverflowError):
        return None

    return parsed.date()


def extract_csv_headers_from_uploaded_file(uploaded: UploadedFile) -> list[str]:
    uploaded.seek(0)
    sample = uploaded.read(64 * 1024)
    uploaded.seek(0)

    try:
        text = sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = sample.decode("utf-8", errors="replace")

    if not text.strip():
        return []

    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t|")
    except Exception:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    headers = next(reader, [])
    return [h.strip() for h in headers if str(h).strip()]
