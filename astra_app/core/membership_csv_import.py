from __future__ import annotations

import csv
import datetime
import io
import logging
import secrets
from typing import Any, override

from django import forms
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from django.utils import timezone
from import_export import fields, resources
from import_export.forms import ConfirmImportForm, ImportForm
from tablib import Dataset

from core.agreements import missing_required_agreements_for_user_in_group
from core.backends import FreeIPAUser
from core.forms_membership import MembershipRequestForm
from core.membership_notes import add_note
from core.membership_request_workflow import approve_membership_request, record_membership_request_created
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


def _norm_header(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def _normalize_email(value: object) -> str:
    return _normalize_str(value).lower()


def _normalize_name(value: object) -> str:
    raw = _normalize_str(value).lower()
    return "".join(ch for ch in raw if ch.isalnum())


def _parse_bool(value: object) -> bool:
    normalized = _normalize_str(value).lower()
    if not normalized:
        return False
    return normalized in {"1", "y", "yes", "true", "t", "active", "activemember", "active member"}


def _parse_date(value: object) -> datetime.datetime | None:
    raw = _normalize_str(value)
    if not raw:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            day = datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        else:
            return datetime.datetime.combine(day, datetime.time(0, 0, 0), tzinfo=datetime.UTC)

    try:
        day = datetime.date.fromisoformat(raw)
    except ValueError:
        return None

    return datetime.datetime.combine(day, datetime.time(0, 0, 0), tzinfo=datetime.UTC)


def _membership_type_matches(value: str, membership_type: MembershipType) -> bool:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return True

    if candidate == membership_type.code.strip().lower():
        return True

    return candidate == membership_type.name.strip().lower()


def _extract_csv_headers_from_uploaded_file(uploaded: UploadedFile) -> list[str]:
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


class MembershipCSVImportForm(ImportForm):
    membership_type = forms.ModelChoiceField(
        queryset=MembershipType.objects.filter(enabled=True).order_by("sort_order", "code"),
        required=True,
        help_text="Membership type to grant for all Active Member rows.",
    )

    email_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the email column. Leave as Auto-detect to infer.",
    )
    name_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the name column. Leave as Auto-detect to infer.",
    )
    active_member_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the active/status column. Leave as Auto-detect to infer.",
    )
    membership_start_date_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the membership start date column. Leave as Auto-detect to infer.",
    )
    committee_notes_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the committee notes column. Leave as Auto-detect to infer.",
    )
    membership_type_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the membership type column. Leave as Auto-detect to infer.",
    )

    enable_name_matching = forms.BooleanField(
        required=False,
        initial=False,
        help_text=(
            "Optional: after attempting email matching, also attempt to match remaining rows by name. "
            "This is flakier and is best used as a second pass on the unmatched export."
        ),
    )

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        format_field = self.fields.get("format")
        if format_field is not None and len(format_field.choices) == 1:
            format_field.initial = format_field.choices[0][0]
            format_field.widget = forms.HiddenInput()

        # Always show question-mapping dropdowns on the initial form. Choices
        # are populated client-side (via JS) and server-side (once a file is
        # posted and headers are known).
        for spec in MembershipRequestForm.all_question_specs():
            field_name = f"{spec.field_name}_column"
            if field_name not in self.fields:
                self.fields[field_name] = forms.ChoiceField(
                    required=False,
                    choices=[("", "Auto-detect")],
                    label=f"{spec.name} answer column",
                    help_text=(
                        "Optional: select which CSV column contains the answer for this membership question. "
                        "Leave as Auto-detect to infer."
                    ),
                )
            self.fields[field_name].widget.attrs["data-preferred-norms"] = "|".join(
                filter(
                    None,
                    (
                        _norm_header(spec.name),
                        _norm_header(spec.field_name),
                        _norm_header(spec.field_name.removeprefix("q_")),
                    ),
                )
            )

        uploaded = self.files.get("import_file")
        if uploaded is None:
            return

        try:
            headers = _extract_csv_headers_from_uploaded_file(uploaded)
        except Exception:
            logger.exception("Unable to read CSV headers for import form dropdowns")
            return

        if not headers:
            return

        choices: list[tuple[str, str]] = [("", "Auto-detect")] + [(h, h) for h in headers]
        for field_name in (
            "email_column",
            "name_column",
            "active_member_column",
            "membership_start_date_column",
            "committee_notes_column",
            "membership_type_column",
        ):
            self.fields[field_name].choices = choices

        for spec in MembershipRequestForm.all_question_specs():
            field_name = f"{spec.field_name}_column"
            self.fields[field_name].choices = choices


class MembershipCSVConfirmImportForm(ConfirmImportForm):
    membership_type = forms.ModelChoiceField(
        queryset=MembershipType.objects.filter(enabled=True).order_by("sort_order", "code"),
        required=True,
        widget=forms.HiddenInput,
    )

    email_column = forms.CharField(required=False, widget=forms.HiddenInput)
    name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    active_member_column = forms.CharField(required=False, widget=forms.HiddenInput)
    membership_start_date_column = forms.CharField(required=False, widget=forms.HiddenInput)
    committee_notes_column = forms.CharField(required=False, widget=forms.HiddenInput)
    membership_type_column = forms.CharField(required=False, widget=forms.HiddenInput)

    enable_name_matching = forms.CharField(required=False, widget=forms.HiddenInput)
    selected_row_numbers = forms.CharField(required=False, widget=forms.HiddenInput)

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        for spec in MembershipRequestForm.all_question_specs():
            field_name = f"{spec.field_name}_column"
            if field_name in self.fields:
                continue
            self.fields[field_name] = forms.CharField(required=False, widget=forms.HiddenInput)


class MembershipCSVImportResource(resources.ModelResource):
    """Import memberships from a CSV using django-import-export's preview/confirm flow.

    The import follows the same workflow as the user-facing site:
    - create a MembershipRequest
    - record a "requested" MembershipLog
    - approve the request (without emailing the user)
    """

    name = fields.Field(attribute="_csv_name", column_name="Name", readonly=True)
    email = fields.Field(attribute="_csv_email", column_name="Email", readonly=True)
    active_member = fields.Field(attribute="_csv_active_member", column_name="Active Member", readonly=True)
    membership_start_date = fields.Field(
        attribute="_csv_membership_start_date",
        column_name="Membership Start Date",
        readonly=True,
    )
    csv_membership_type = fields.Field(
        attribute="_csv_membership_type",
        column_name="Membership Type",
        readonly=True,
    )
    committee_notes = fields.Field(attribute="_csv_committee_notes", column_name="Committee Notes", readonly=True)
    matched_username = fields.Field(attribute="_matched_username", column_name="Matched Username", readonly=True)
    decision = fields.Field(attribute="_decision", column_name="Decision", readonly=True)
    decision_reason = fields.Field(attribute="_decision_reason", column_name="Decision Reason", readonly=True)

    def __init__(
        self,
        *,
        membership_type: MembershipType | None = None,
        actor_username: str = "",
        enable_name_matching: bool = False,
        email_column: str = "",
        name_column: str = "",
        active_member_column: str = "",
        membership_start_date_column: str = "",
        committee_notes_column: str = "",
        membership_type_column: str = "",
        question_column_overrides: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._membership_type = membership_type
        self._actor_username = actor_username

        self._enable_name_matching = bool(enable_name_matching)

        self._email_column_override = email_column
        self._name_column_override = name_column
        self._active_member_column_override = active_member_column
        self._membership_start_date_column_override = membership_start_date_column
        self._committee_notes_column_override = committee_notes_column
        self._membership_type_column_override = membership_type_column
        self._question_column_overrides = question_column_overrides or {}

        self._headers: list[str] = []
        self._email_header: str | None = None
        self._name_header: str | None = None
        self._active_header: str | None = None
        self._start_header: str | None = None
        self._note_header: str | None = None
        self._type_header: str | None = None

        self._question_header_by_name: dict[str, str | None] = {}

        self._email_to_usernames: dict[str, set[str]] = {}
        self._email_lookup_cache: dict[str, set[str]] = {}
        self._unmatched: list[dict[str, str]] = []

        self._name_to_usernames: dict[str, set[str]] = {}

        self._csv_total_records = 0
        self._matched_by_email = 0
        self._matched_by_name = 0

        # Operator visibility: keep counts so we can summarize why rows are skipped.
        self._decision_counts: dict[str, int] = {}
        self._skip_reason_counts: dict[str, int] = {}

    class Meta:
        model = MembershipRequest
        # We create new MembershipRequests for imported rows (or reuse an
        # existing pending request for the same user+type). Setting this avoids
        # ModelInstanceLoader trying to resolve an instance via the model's
        # default id field (which is not in the CSV).
        import_id_fields = ()
        fields = (
            "name",
            "email",
            "active_member",
            "membership_start_date",
            "csv_membership_type",
            "committee_notes",
            "matched_username",
            "decision",
            "decision_reason",
        )
        # FreeIPA operations can't be rolled back, so using a single DB
        # transaction for the full import is counterproductive: one failing row
        # would rollback DB changes while leaving FreeIPA side-effects applied.
        use_transactions = False

        # IMPORTANT: this import has per-row side-effects (FreeIPA + audit logs)
        # implemented in after_save_instance(). If django-import-export is
        # configured globally to use bulk inserts/updates, it can bypass these
        # hooks. Force per-row saves so the confirm step reliably applies.
        use_bulk = False

    @override
    def import_row(self, row: Any, instance_loader: Any, **kwargs: Any) -> Any:
        try:
            return super().import_row(row, instance_loader, **kwargs)
        except Exception:
            # import-export can swallow exceptions into RowResult without
            # calling after_import_row()/after_save_instance(). Log here so the
            # operator always gets a traceback for "error=N" totals.
            row_number = kwargs.get("row_number")
            email = ""
            matched_username = ""
            decision = "UNKNOWN"
            reason = ""
            try:
                email = self._row_email(row)
                matched_username = self._row_username(row)
                decision, reason = self._decision_for_row(row)
            except Exception as exc:
                reason = f"diagnostics failed: {exc!r}"

            logger.exception(
                "Membership CSV import: row crashed row=%s email=%r username=%r decision=%s reason=%r dry_run=%s",
                row_number,
                email,
                matched_username,
                decision,
                reason,
                bool(kwargs.get("dry_run")),
            )
            raise

    @override
    def before_import(self, dataset: Dataset, **kwargs: Any) -> None:
        if self._membership_type is None:
            raise ValueError("membership_type is required")
        self._unmatched = []
        self._decision_counts = {}
        self._skip_reason_counts = {}

        try:
            self._csv_total_records = len(dataset)
        except Exception:
            self._csv_total_records = 0
        self._matched_by_email = 0
        self._matched_by_name = 0

        headers = list(dataset.headers or [])
        if not headers:
            raise ValueError("CSV has no headers")

        self._headers = headers
        header_by_norm = {_norm_header(h): h for h in headers if h}

        def _resolve_override(override: str) -> str | None:
            raw = (override or "").strip()
            if not raw:
                return None
            if raw in headers:
                return raw
            norm = _norm_header(raw)
            if norm in header_by_norm:
                return header_by_norm[norm]
            raise ValueError(f"Column '{raw}' not found in CSV headers")

        self._email_header = _resolve_override(self._email_column_override) or (
            header_by_norm.get("email")
            or header_by_norm.get("emailaddress")
            or header_by_norm.get("mail")
        )
        self._name_header = _resolve_override(self._name_column_override) or (
            header_by_norm.get("name") or header_by_norm.get("fullname")
        )
        self._active_header = _resolve_override(self._active_member_column_override) or (
            header_by_norm.get("activemember")
            or header_by_norm.get("active")
            or header_by_norm.get("status")
        )
        self._start_header = _resolve_override(self._membership_start_date_column_override) or (
            header_by_norm.get("membershipstartdate") or header_by_norm.get("startdate")
        )
        self._note_header = _resolve_override(self._committee_notes_column_override) or (
            header_by_norm.get("committeenotes")
            or header_by_norm.get("committeenote")
            or header_by_norm.get("fasstatusnote")
            or header_by_norm.get("note")
            or header_by_norm.get("notes")
        )
        self._type_header = _resolve_override(self._membership_type_column_override) or (
            header_by_norm.get("membershiptype") or header_by_norm.get("type")
        )

        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        self._question_header_by_name = {}
        for spec in MembershipRequestForm.question_specs_for_membership_type(membership_type):
            key = f"{spec.field_name}_column"
            override = self._question_column_overrides.get(key, "")
            resolved = _resolve_override(override) if override else None
            if resolved is None:
                resolved = (
                    header_by_norm.get(_norm_header(spec.name))
                    or header_by_norm.get(_norm_header(spec.field_name))
                    or header_by_norm.get(_norm_header(spec.field_name.removeprefix("q_")))
                )
            self._question_header_by_name[spec.name] = resolved

        if self._email_header is None:
            raise ValueError("CSV must include an Email column")

        self._email_lookup_cache = {}

        # Prefer a full (cached) directory scan for large imports, but fall
        # back to per-email search if listing is unavailable in this deployment.
        self._email_to_usernames = {}
        self._name_to_usernames = {}
        users = FreeIPAUser.all()
        if not users:
            logger.warning(
                "Membership CSV import: FreeIPAUser.all() returned 0 users; email matching will use per-email search"
            )
        for user in users:
            email = _normalize_email(user.email)
            username = _normalize_str(user.username)
            if not email or not username:
                continue
            self._email_to_usernames.setdefault(email, set()).add(username)

            if self._enable_name_matching:
                key = _normalize_name(user.full_name)
                if key:
                    self._name_to_usernames.setdefault(key, set()).add(username)

        logger.info(
            "Membership CSV import: headers=%d email_header=%r name_header=%r active_header=%r start_header=%r note_header=%r type_header=%r freeipa_users=%d unique_emails=%d",
            len(headers),
            self._email_header,
            self._name_header,
            self._active_header,
            self._start_header,
            self._note_header,
            self._type_header,
            len(users),
            len(self._email_to_usernames),
        )

        question_columns = {name: header for name, header in self._question_header_by_name.items() if header}
        if question_columns:
            logger.info(
                "Membership CSV import: question_columns=%r",
                question_columns,
            )

        if self._active_header is None:
            logger.warning(
                "Membership CSV import: no Active/Status column detected; all rows are treated as active"
            )

    def _usernames_for_email(self, email: str) -> set[str]:
        normalized = (email or "").strip().lower()
        if not normalized:
            return set()

        cached = self._email_lookup_cache.get(normalized)
        if cached is not None:
            return cached

        # If the directory listing worked, use it.
        if self._email_to_usernames:
            usernames = set(self._email_to_usernames.get(normalized, set()))
            self._email_lookup_cache[normalized] = usernames
            # Email is PII; keep this at DEBUG level.
            logger.debug(
                "Membership CSV import: email lookup via directory email=%r matches=%r",
                normalized,
                sorted(usernames),
            )
            return usernames

        # Fallback: do a targeted lookup (robust when the service account
        # lacks permission to list all users).
        user = FreeIPAUser.find_by_email(normalized)
        usernames = {user.username} if user and user.username else set()
        self._email_lookup_cache[normalized] = usernames
        # Email is PII; keep this at DEBUG level.
        logger.debug(
            "Membership CSV import: email lookup via find_by_email email=%r match=%r",
            normalized,
            next(iter(usernames), ""),
        )
        return usernames

    def _usernames_for_name(self, name: str) -> set[str]:
        if not self._enable_name_matching:
            return set()

        key = _normalize_name(name)
        if not key:
            return set()
        return set(self._name_to_usernames.get(key, set()))

    def _match_username_for_row(self, row: Any) -> tuple[str, str, str]:
        """Return (username, method, reason).

        method is "email" or "name".
        """

        email = self._row_email(row)
        usernames = self._usernames_for_email(email)
        if usernames:
            if len(usernames) > 1:
                return ("", "email", f"Ambiguous email (matches {len(usernames)} users)")
            return (next(iter(usernames)), "email", "")

        if not self._enable_name_matching:
            return ("", "", "No FreeIPA user with this email")

        name = self._row_name(row)
        if not name:
            return ("", "", "No FreeIPA user with this email")

        usernames = self._usernames_for_name(name)
        if not usernames:
            return ("", "", "No FreeIPA user with this email or name")
        if len(usernames) > 1:
            return ("", "name", f"Ambiguous name (matches {len(usernames)} users)")
        return (next(iter(usernames)), "name", "")

    def _row_value(self, row: Any, header: str | None) -> object:
        if header is None:
            return ""
        try:
            return row.get(header, "")
        except AttributeError:
            return ""

    def _row_email(self, row: Any) -> str:
        if self._email_header is None:
            return ""
        return _normalize_email(self._row_value(row, self._email_header))

    def _row_name(self, row: Any) -> str:
        return _normalize_str(self._row_value(row, self._name_header))

    def _row_is_active(self, row: Any) -> bool:
        # Some membership exports omit an explicit active/status column and
        # implicitly represent only active members. In that case, treat rows as
        # eligible rather than skipping everything.
        if self._active_header is None:
            return True
        return _parse_bool(self._row_value(row, self._active_header))

    def _row_approved_at(self, row: Any) -> datetime.datetime | None:
        if self._start_header is None:
            return None
        return _parse_date(self._row_value(row, self._start_header))

    def _row_note(self, row: Any) -> str:
        return _normalize_str(self._row_value(row, self._note_header))

    def _row_csv_membership_type(self, row: Any) -> str:
        if self._type_header is None:
            return ""
        return _normalize_str(self._row_value(row, self._type_header))

    def _decision_for_row(self, row: Any) -> tuple[str, str]:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        email = self._row_email(row)
        if not email:
            return ("SKIP", "Missing Email")

        username, _method, reason = self._match_username_for_row(row)
        if not username:
            return ("SKIP", reason or "No match")

        if not self._row_is_active(row):
            return ("SKIP", "Not an Active Member")

        now = timezone.now()
        if Membership.objects.filter(
            target_username=username,
            membership_type=membership_type,
            expires_at__gt=now,
        ).exists():
            return ("SKIP", "Active membership already exists")

        raw_type = self._row_csv_membership_type(row)
        if raw_type and not _membership_type_matches(raw_type, membership_type):
            return (
                "SKIP",
                f"CSV type '{raw_type}' does not match selected '{membership_type.code}'",
            )

        missing = missing_required_agreements_for_user_in_group(username, membership_type.group_cn)
        if missing:
            return (
                "SKIP",
                f"Missing required agreements for '{membership_type.group_cn}': {', '.join(missing)}",
            )

        return ("IMPORT", "")

    def _populate_preview_fields(self, instance: MembershipRequest, row: Any) -> None:
        instance._csv_name = self._row_name(row)
        instance._csv_email = self._row_email(row)
        instance._csv_active_member = _normalize_str(self._row_value(row, self._active_header))
        instance._csv_membership_start_date = _normalize_str(self._row_value(row, self._start_header))
        instance._csv_membership_type = self._row_csv_membership_type(row)
        instance._csv_committee_notes = self._row_note(row)

        instance._matched_username = self._row_username(row)

        decision, reason = self._decision_for_row(row)
        instance._decision = decision
        instance._decision_reason = reason

    def _row_responses(self, row: Any) -> list[dict[str, str]]:
        if not self._headers:
            return []

        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        question_responses: list[dict[str, str]] = []
        used_norms: set[str] = set()
        for spec in MembershipRequestForm.question_specs_for_membership_type(membership_type):
            header = self._question_header_by_name.get(spec.name)
            if not header:
                continue
            used_norms.add(_norm_header(header))
            value = _normalize_str(self._row_value(row, header))
            if value or spec.required:
                question_responses.append({spec.name: value})

        reserved_norms = {
            _norm_header(self._email_header) if self._email_header else "",
            _norm_header(self._name_header) if self._name_header else "",
            _norm_header(self._active_header) if self._active_header else "",
            _norm_header(self._start_header) if self._start_header else "",
            _norm_header(self._note_header) if self._note_header else "",
            _norm_header(self._type_header) if self._type_header else "",
        }

        reserved_norms |= used_norms

        responses: list[dict[str, str]] = list(question_responses)
        for header in self._headers:
            if not header or _norm_header(header) in reserved_norms:
                continue
            value = _normalize_str(self._row_value(row, header))
            if value:
                responses.append({header: value})
        return responses

    def _row_username(self, row: Any) -> str:
        username, _method, _reason = self._match_username_for_row(row)
        return username

    def _record_unmatched(self, *, row: Any, reason: str) -> None:
        item: dict[str, str] = {}
        for header in self._headers:
            if not header:
                continue
            item[header] = _normalize_str(self._row_value(row, header))
        item["reason"] = reason
        self._unmatched.append(item)

    @override
    def before_import_row(self, row: Any, **kwargs: Any) -> None:
        row_number = kwargs.get("row_number")
        email = self._row_email(row)
        if not email:
            # Treat blank/empty lines as no-ops.
            return

        username, method, reason = self._match_username_for_row(row)
        usernames = {username} if username else set()
        if isinstance(row_number, int) and row_number <= 50:
            # Email is PII; keep this at DEBUG level.
            logger.debug(
                "Membership CSV import: row=%d email=%r usernames=%r",
                row_number,
                email,
                sorted(usernames),
            )

        if username and method == "email":
            self._matched_by_email += 1
        elif username and method == "name":
            self._matched_by_name += 1

        if not username:
            self._record_unmatched(row=row, reason=reason or "No match")
            return

        # Don't block the import preview/confirm flow for per-row business rules.
        # These rows will be skipped during import.

    @override
    def skip_row(self, instance: Any, original: Any, row: Any, import_validation_errors: Any = None) -> bool:
        decision, reason = self._decision_for_row(row)
        self._decision_counts[decision] = self._decision_counts.get(decision, 0) + 1
        if decision != "IMPORT" and reason:
            self._skip_reason_counts[reason] = self._skip_reason_counts.get(reason, 0) + 1

        row_number = getattr(row, "number", None)
        # Email is PII; keep row-level decisions at DEBUG level.
        if isinstance(row_number, int) and row_number <= 50:
            logger.debug(
                "Membership CSV import: decision row=%d decision=%s reason=%r",
                row_number,
                decision,
                reason,
            )

        return decision != "IMPORT"

    @override
    def save_instance(self, instance: Any, is_create: bool, row: Any, **kwargs: Any) -> None:
        # The preview step runs with dry_run=True. Because this Resource opts out
        # of DB transactions (FreeIPA side-effects can't be rolled back), we
        # must also ensure that preview does not persist MembershipRequest rows.
        if bool(kwargs.get("dry_run")):
            return
        super().save_instance(instance, is_create, row, **kwargs)

    @override
    def import_instance(self, instance: MembershipRequest, row: Any, **kwargs: Any) -> None:
        super().import_instance(instance, row, **kwargs)

        self._populate_preview_fields(instance, row)

        # import_instance is called even for rows that will later be skipped.
        # Only set required fields for rows we intend to validate/save.
        decision, _reason = self._decision_for_row(row)
        if decision != "IMPORT":
            return

        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        username, _method, _reason = self._match_username_for_row(row)
        if not username:
            return

        responses = self._row_responses(row)

        existing_pending = (
            MembershipRequest.objects.filter(
                requested_username=username,
                membership_type=membership_type,
                status=MembershipRequest.Status.pending,
            )
            # When re-using an existing pending request, we must carry over
            # requested_at. Otherwise, the import-export save() call will issue
            # an UPDATE with requested_at=NULL (because this Resource starts
            # from a fresh instance and then assigns pk).
            .only("pk", "responses", "requested_at")
            .first()
        )
        instance._csv_created_request = existing_pending is None
        if existing_pending is not None:
            instance.pk = existing_pending.pk
            instance.requested_at = existing_pending.requested_at

        merged_responses: list[dict[str, str]] = []
        if existing_pending is not None and isinstance(existing_pending.responses, list):
            merged_responses.extend(existing_pending.responses)
        for item in responses:
            if item not in merged_responses:
                merged_responses.append(item)

        instance.requested_username = username
        instance.requested_organization = None
        instance.requested_organization_code = ""
        instance.requested_organization_name = ""
        instance.membership_type = membership_type
        instance.status = MembershipRequest.Status.pending
        instance.responses = merged_responses

    @override
    def after_save_instance(self, instance: MembershipRequest, row: Any, **kwargs: Any) -> None:
        super().after_save_instance(instance, row, **kwargs)

        if bool(kwargs.get("dry_run")):
            return

        row_number = kwargs.get("row_number")
        email = self._row_email(row)
        username = instance.requested_username

        # This runs only during the confirm step (dry_run=False). Keep a clear,
        # INFO-level breadcrumb per row so production logs show that approvals
        # are being attempted even when DEBUG is disabled.
        logger.info(
            "Membership CSV import: apply start row=%s email=%r username=%r membership_type=%s",
            row_number,
            email,
            username,
            instance.membership_type_id,
        )

        start_at = self._row_approved_at(row)
        now = timezone.now().astimezone(datetime.UTC)
        if start_at is None:
            start_at = now

        # The CSV start date is the membership's "effective since" time.
        # However, if the start date is in the past (e.g. a "member since"
        # field), treating it as the approval timestamp would immediately
        # expire memberships (because expiry is derived from approval time).
        #
        # Use "now" for approval/expiry when the start date is in the past,
        # while still backfilling created/request times from the CSV.
        decided_at = max(start_at, now)

        try:
            # The importer may re-use an existing pending request for the same
            # user+type. To keep this workflow idempotent (and robust against
            # retries), only create the "requested" log if it doesn't exist yet.
            if not MembershipLog.objects.filter(
                membership_request=instance,
                action=MembershipLog.Action.requested,
            ).exists():
                record_membership_request_created(
                    membership_request=instance,
                    actor_username=self._actor_username,
                    send_submitted_email=False,
                )

            approve_membership_request(
                membership_request=instance,
                actor_username=self._actor_username,
                send_approved_email=False,
                decided_at=decided_at,
            )

            # requested_at is auto_now_add, so Django overwrites it on create.
            # For CSV imports we want request time to reflect the CSV start date
            # (or now if none was provided).
            MembershipRequest.objects.filter(pk=instance.pk).update(requested_at=start_at)

            # `created_at` is auto_now_add; backfill the membership start date from the CSV.
            Membership.objects.filter(
                target_username=instance.requested_username,
                membership_type=instance.membership_type,
            ).update(created_at=start_at)

            # Only record the import note after a fully successful apply. This
            # avoids leaving misleading "[Import]" notes behind when approval
            # fails (e.g. FreeIPA group add failure).
            csv_note = self._row_note(row)
            if csv_note:
                add_note(
                    membership_request=instance,
                    username=self._actor_username,
                    content=f"[Import] {csv_note}",
                )
        except Exception:
            logger.exception(
                "Membership CSV import: apply failed row=%s email=%r username=%r membership_type=%s",
                row_number,
                email,
                username,
                instance.membership_type_id,
            )
            raise

        logger.info(
            "Membership CSV import: apply success row=%s email=%r username=%r membership_type=%s",
            row_number,
            email,
            username,
            instance.membership_type_id,
        )

    @override
    def after_import_row(self, row: Any, row_result: Any, **kwargs: Any) -> None:
        super().after_import_row(row, row_result, **kwargs)

        # import-export does not log row failures by default. RowResult tells us
        # that a row failed, but in import-export 4.3.x the traceback is stored
        # on Result.row_errors (logged in after_import()).
        if not getattr(row_result, "is_error", lambda: False)():
            return

        row_number = kwargs.get("row_number")
        email = self._row_email(row)
        matched_username = self._row_username(row)
        try:
            decision, reason = self._decision_for_row(row)
        except Exception as exc:
            # Don't let diagnostics crash the import; this hook is best-effort.
            logger.exception(
                "Membership CSV import: failed to compute decision for row error logging row=%s email=%r username=%r",
                row_number,
                email,
                matched_username,
            )
            decision = "UNKNOWN"
            reason = f"decision exception: {exc!r}"

        logger.error(
            "Membership CSV import: row error row=%s email=%r username=%r decision=%s reason=%r",
            row_number,
            email,
            matched_username,
            decision,
            reason,
        )

        validation_error = getattr(row_result, "validation_error", None)
        if validation_error is not None:
            logger.error(
                "Membership CSV import: row validation_error row=%s email=%r username=%r error=%r",
                row_number,
                email,
                matched_username,
                validation_error,
            )

        # Detailed tracebacks are logged in after_import() from Result.row_errors.

    @override
    def after_import(self, dataset: Dataset, result: Any, **kwargs: Any) -> None:
        super().after_import(dataset, result, **kwargs)

        matched_total = self._matched_by_email + self._matched_by_name
        total = int(self._csv_total_records or 0)
        percent = round((matched_total / total) * 100.0, 1) if total > 0 else 0.0
        setattr(result, "csv_total_records", total)
        setattr(result, "matched_by_email", int(self._matched_by_email))
        setattr(result, "matched_by_name", int(self._matched_by_name))
        setattr(result, "matched_total", int(matched_total))
        setattr(result, "matched_total_percent", float(percent))

        try:
            totals = dict(getattr(result, "totals", {}) or {})
        except Exception:
            totals = {}

        if totals:
            logger.info(
                "Membership CSV import result totals: %s",
                " ".join(f"{k}={totals[k]}" for k in sorted(totals)),
            )

        # In import-export 4.3.x, per-row exception tracebacks are stored on
        # Result.row_errors (not on RowResult). Always surface these at ERROR so
        # operators can diagnose why totals include error=N.
        row_errors_obj = getattr(result, "row_errors", None)
        # In import-export 4.3.x, Result.row_errors() returns:
        #   list[tuple[int, list[Error]]]
        # Newer versions may expose Error objects directly.
        row_errors_pairs: list[tuple[int, list[Any]]] = []
        row_errors_flat: list[Any] = []

        if callable(row_errors_obj):
            try:
                raw = list(row_errors_obj())
            except TypeError:
                raw = []

            if raw and isinstance(raw[0], tuple) and len(raw[0]) == 2:
                # 4.3.x shape
                row_errors_pairs = [(int(n), list(errs or [])) for n, errs in raw]
            else:
                row_errors_flat = raw
        else:
            row_errors_flat = list(row_errors_obj or [])

        if row_errors_pairs:
            limit = 25
            shown = 0
            for row_number, errors in row_errors_pairs:
                for err in errors:
                    shown += 1
                    if shown > limit:
                        break

                    err_row = getattr(err, "row", None)
                    try:
                        email = self._row_email(err_row)
                        matched_username = self._row_username(err_row)
                    except Exception:
                        email = ""
                        matched_username = ""

                    logger.error(
                        "Membership CSV import: row exception row=%s email=%r username=%r exc=%r\n%s",
                        row_number,
                        email,
                        matched_username,
                        getattr(err, "error", None),
                        getattr(err, "traceback", ""),
                    )

                if shown > limit:
                    break

            total = sum(len(errs) for _n, errs in row_errors_pairs)
            if total > limit:
                logger.error(
                    "Membership CSV import: %d more row exceptions not shown",
                    total - limit,
                )

        elif row_errors_flat:
            # Best-effort fallback for non-4.3.x shapes.
            limit = 25
            for err in row_errors_flat[:limit]:
                err_row = getattr(err, "row", None)
                row_number = getattr(err, "number", None)
                try:
                    email = self._row_email(err_row)
                    matched_username = self._row_username(err_row)
                except Exception:
                    email = ""
                    matched_username = ""

                logger.error(
                    "Membership CSV import: row exception row=%s email=%r username=%r exc=%r\n%s",
                    row_number,
                    email,
                    matched_username,
                    getattr(err, "error", None),
                    getattr(err, "traceback", ""),
                )

            if len(row_errors_flat) > limit:
                logger.error(
                    "Membership CSV import: %d more row exceptions not shown",
                    len(row_errors_flat) - limit,
                )

        # Summarize outcomes to make it easy to diagnose why a run is all "SKIP".
        decision_summary = " ".join(
            f"{k}={self._decision_counts[k]}" for k in sorted(self._decision_counts)
        )
        if decision_summary:
            logger.info(
                "Membership CSV import summary: %s unmatched=%d dry_run=%s",
                decision_summary,
                len(self._unmatched),
                bool(kwargs.get("dry_run")),
            )

        if self._skip_reason_counts:
            top = sorted(self._skip_reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            logger.info(
                "Membership CSV import skip reasons (top %d): %s",
                len(top),
                "; ".join(f"{reason} ({count})" for reason, count in top),
            )

        if not self._unmatched:
            return

        out = Dataset()
        headers = [h for h in self._headers if h]
        reason_header = "reason"
        if any(_norm_header(h) == "reason" for h in headers):
            # Avoid clobbering an existing input column.
            reason_header = "unmatched_reason"

        out.headers = [*headers, reason_header]
        for item in self._unmatched:
            out.append([*(item.get(h, "") for h in headers), item.get("reason", "")])

        token = secrets.token_urlsafe(16)
        cache_key = f"membership-import-unmatched:{token}"
        cache.set(cache_key, out.export("csv"), timeout=60 * 60)

        download_url = reverse(
            "admin:core_membershipcsvimportlink_download_unmatched",
            kwargs={"token": token},
        )
        setattr(result, "unmatched_download_url", download_url)
