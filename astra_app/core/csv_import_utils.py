import csv
import datetime
import io
import logging
import secrets
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, override

from dateutil import parser
from django import forms
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from import_export.forms import ConfirmImportForm, ImportForm
from tablib import Dataset

from core.forms_membership import MembershipRequestForm
from core.logging_extras import current_exception_log_fields
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)

AUTO_DETECT_CHOICE: tuple[str, str] = ("", "Auto-detect")


def build_csv_header_choices(headers: Sequence[str]) -> list[tuple[str, str]]:
    return [AUTO_DETECT_CHOICE, *[(header, header) for header in headers]]


def get_result_attr[T](result: Any, attr_name: str, default: T) -> Any | T:
    # import-export Result objects are duck-typed and some optional attributes
    # are missing or raise during access depending on the execution path.
    try:
        value = getattr(result, attr_name)
    except Exception:
        return default
    return default if value is None else value


def get_result_totals(result: Any) -> dict[str, Any]:
    totals = get_result_attr(result, "totals", {})
    try:
        return dict(totals or {})
    except Exception:
        return {}


def _coerce_result_sequence(value: Any) -> list[Any]:
    if callable(value):
        try:
            value = value()
        except TypeError:
            return []

    try:
        return list(value or [])
    except TypeError:
        return []


def get_result_rows(
    result: Any,
    attr_name: str = "valid_rows",
    *,
    fallback_attr_name: str | None = None,
) -> list[Any]:
    rows = _coerce_result_sequence(get_result_attr(result, attr_name, None))
    if rows or fallback_attr_name is None:
        return rows
    return _coerce_result_sequence(get_result_attr(result, fallback_attr_name, None))


def get_result_row_errors(result: Any) -> list[tuple[int | None, Any]]:
    """Each row error with its row number.

    import-export 4.3.x returns list[tuple[int, list[Error]]] from Result.row_errors(), while
    other versions expose Error objects directly; both shapes are flattened here.
    """
    raw = _coerce_result_sequence(get_result_attr(result, "row_errors", None))
    try:
        return [(int(row_number), error) for row_number, errors in raw for error in errors or []]
    except (TypeError, ValueError):
        # Error objects carry their own row number, when they have one.
        return [(getattr(error, "number", None), error) for error in raw]


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


# Header aliases for the membership-term columns both membership importers read.
TERM_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "membership_start_date": ("membershipstartdate", "startdate"),
    "membership_end_date": (
        "membershipenddate",
        "enddate",
        "membershipexpirydate",
        "membershipexpirationdate",
        "expirydate",
        "expirationdate",
    ),
}


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
    *,
    content_attr_name: str = "unmatched_csv_content",
    url_attr_name: str = "unmatched_download_url",
) -> None:
    token = secrets.token_urlsafe(16)
    cache_key = f"{cache_key_prefix}:{token}"
    csv_content = dataset.export("csv")
    cache.set(cache_key, csv_content, timeout=60 * 60)

    download_url = reverse(reverse_url_name, kwargs={"token": token})
    # `Result` is a third-party import-export type with no extension hook;
    # dynamic attributes are used as a lightweight duck-typed contract.
    setattr(result, content_attr_name, csv_content)
    setattr(result, url_attr_name, download_url)


def sanitize_csv_cell(value: str) -> str:
    """Prefix formula-starting characters to prevent spreadsheet formula injection."""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return value


def normalize_csv_email(value: object) -> str:
    return _normalize_str(value).lower()


def normalize_csv_name(value: object) -> str:
    # casefold() provides stronger Unicode-insensitive matching than lower().
    raw = _normalize_str(value).casefold()
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

    text = ""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if not text.strip():
        return []

    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t|")
    except Exception:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    headers = next(reader, [])
    return [h.strip() for h in headers if str(h).strip()]


def _question_column_field_names() -> tuple[str, ...]:
    return tuple(f"{spec.field_name}_column" for spec in MembershipRequestForm.all_question_specs())


class CSVColumnImportForm(ImportForm):
    """Import form whose column dropdowns offer the uploaded file's headers.

    Subclasses name their dropdowns in column_field_names; with_question_columns adds one
    for each membership question's answer.
    """

    column_field_names: ClassVar[tuple[str, ...]] = ()
    with_question_columns: ClassVar[bool] = False

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        format_field = self.fields.get("format")
        if format_field is not None and len(format_field.choices) == 1:
            format_field.initial = format_field.choices[0][0]
            format_field.widget = forms.HiddenInput()

        field_names = self.column_field_names
        if self.with_question_columns:
            # Always shown; choices are filled client-side (JS) and below once a file is posted.
            for spec in MembershipRequestForm.all_question_specs():
                field = self.fields.setdefault(
                    f"{spec.field_name}_column",
                    forms.ChoiceField(
                        required=False,
                        choices=[AUTO_DETECT_CHOICE],
                        label=f"{spec.name} answer column",
                        help_text=(
                            "Optional: select which CSV column contains the answer for this membership question. "
                            "Leave as Auto-detect to infer."
                        ),
                    ),
                )
                preferred = (spec.title, spec.name, *spec.csv_header_aliases, spec.field_name, spec.field_name.removeprefix("q_"))
                field.widget.attrs["data-preferred-norms"] = "|".join(filter(None, map(norm_csv_header, preferred)))
            field_names = (*field_names, *_question_column_field_names())

        uploaded = self.files.get("import_file")
        if uploaded is None:
            return
        try:
            headers = extract_csv_headers_from_uploaded_file(uploaded)
        except Exception:
            logger.exception("Unable to read CSV headers for import form dropdowns", extra=current_exception_log_fields())
            return
        if headers:
            set_form_column_field_choices(form=self, field_names=field_names, headers=headers)


class QuestionColumnsConfirmImportForm(ConfirmImportForm):
    """Confirm form that carries each membership question's chosen answer column."""

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field_name in _question_column_field_names():
            self.fields.setdefault(field_name, forms.CharField(required=False, widget=forms.HiddenInput))


class MembershipCSVImportLoggingMixin:
    """Batch ids and operator logging shared by the membership CSV import resources.

    Logs go to the subclass module's logger, under log_label.
    """

    log_label: ClassVar[str]
    _import_batch_id: uuid.UUID | None
    _decision_counts: dict[str, int]
    _skip_reason_counts: dict[str, int]
    _unmatched: list[dict[str, str]]

    def _row_error_identity(self, row: Any) -> str:
        """Identifies the CSV row of a row exception in the logs."""
        raise NotImplementedError

    def import_data(self, dataset: Dataset, dry_run: bool = False, raise_errors: bool = False, **kwargs: Any) -> Any:
        if not dry_run:
            self._import_batch_id = uuid.uuid4()
        rows_total = len(dataset)

        result: Any | None = None
        outcome = "applied"
        try:
            result = super().import_data(dataset, dry_run=dry_run, raise_errors=raise_errors, **kwargs)
            return result
        except Exception:
            outcome = "failed"
            raise
        finally:
            if not dry_run:
                rows_applied = 0
                rows_failed = rows_total if outcome == "failed" else 0
                if result is not None:
                    totals = get_result_totals(result)
                    rows_applied = sum(int(totals.get(key, 0)) for key in ("new", "update", "delete"))
                    rows_failed = int(totals.get("error", 0)) + int(totals.get("invalid", 0))

                fields = {
                    "event": "astra.membership.csv_import.batch_applied",
                    "component": "membership",
                    "outcome": outcome,
                    "correlation_id": str(self._import_batch_id),
                    "batch_id": self._import_batch_id,
                    "rows_total": rows_total,
                    "rows_applied": rows_applied,
                    "rows_failed": rows_failed,
                }
                logging.getLogger(type(self).__module__).info(
                    " ".join(f"{key}={value}" for key, value in fields.items()), extra=fields
                )
            self._import_batch_id = None

    def _log_import_result(self, result: Any, *, dry_run: bool) -> None:
        resource_logger = logging.getLogger(type(self).__module__)
        totals = get_result_totals(result)
        if totals:
            resource_logger.info(
                "%s result totals: %s", self.log_label, " ".join(f"{key}={totals[key]}" for key in sorted(totals))
            )

        # Per-row tracebacks live on Result.row_errors, not on RowResult; surface them at ERROR
        # so operators can see why totals include error=N.
        row_errors = get_result_row_errors(result)
        limit = 25
        for row_number, error in row_errors[:limit]:
            # Error objects differ across import-export versions.
            try:
                identity = self._row_error_identity(getattr(error, "row", None))
            except Exception:
                identity = ""
            resource_logger.error(
                "%s: row exception row=%s %s exc=%r\n%s",
                self.log_label,
                row_number,
                identity,
                getattr(error, "error", None),
                getattr(error, "traceback", ""),
            )
        if len(row_errors) > limit:
            resource_logger.error("%s: %d more row exceptions not shown", self.log_label, len(row_errors) - limit)

        # Summarize outcomes to make it easy to diagnose why a run is all "SKIP".
        if self._decision_counts:
            resource_logger.info(
                "%s summary: %s unmatched=%d dry_run=%s",
                self.log_label,
                " ".join(f"{key}={self._decision_counts[key]}" for key in sorted(self._decision_counts)),
                len(self._unmatched),
                dry_run,
            )
        if self._skip_reason_counts:
            top = sorted(self._skip_reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            resource_logger.info(
                "%s skip reasons (top %d): %s",
                self.log_label,
                len(top),
                "; ".join(f"{reason} ({count})" for reason, count in top),
            )
