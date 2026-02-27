import datetime
import logging
import uuid
from dataclasses import dataclass
from typing import Any, override

from django import forms
from django.utils import timezone
from import_export import fields, resources
from import_export.forms import ConfirmImportForm, ImportForm
from tablib import Dataset

from core.agreements import missing_required_agreements_for_user_in_group
from core.csv_import_utils import (
    attach_unmatched_csv_to_result,
    extract_csv_headers_from_uploaded_file,
    norm_csv_header,
    normalize_csv_email,
    normalize_csv_name,
    parse_csv_bool,
    parse_csv_date,
    resolve_column_header,
    sanitize_csv_cell,
    set_form_column_field_choices,
)
from core.forms_membership import MembershipRequestForm
from core.freeipa.user import FreeIPAUser
from core.membership_notes import add_note
from core.membership_request_workflow import approve_membership_request, record_membership_request_created
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Note
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RowDecision:
    decision: str
    reason: str
    username: str
    match_method: str
    start_at: datetime.datetime | None
    end_at: datetime.datetime | None
    row_note: str
    responses: list[dict[str, str]]

# Column-mapping field names shared between MembershipCSVImportForm,
# MembershipCSVConfirmImportForm, and the admin's get_confirm_form_initial /
# get_import_resource_kwargs.  Adding a new column requires a single edit here.
_COLUMN_FIELDS = (
    "email_column",
    "name_column",
    "active_member_column",
    "membership_start_date_column",
    "membership_end_date_column",
    "committee_notes_column",
    "membership_type_column",
)

_USER_IMPORT_MEMBERSHIP_TYPES = (
    MembershipType.objects.enabled()
    .filter(category__is_individual=True)
    .ordered_for_display()
)


def _membership_type_matches(value: str, membership_type: MembershipType) -> bool:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return True

    if candidate == membership_type.code.strip().lower():
        return True

    return candidate == membership_type.name.strip().lower()
class MembershipCSVImportForm(ImportForm):
    membership_type = forms.ModelChoiceField(
        queryset=_USER_IMPORT_MEMBERSHIP_TYPES,
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
    membership_end_date_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text="Optional: select the CSV header for the membership end date column. Leave as Auto-detect to infer.",
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
                        norm_csv_header(spec.name),
                        norm_csv_header(spec.field_name),
                        norm_csv_header(spec.field_name.removeprefix("q_")),
                    ),
                )
            )

        uploaded = self.files.get("import_file")
        if uploaded is None:
            return

        try:
            headers = extract_csv_headers_from_uploaded_file(uploaded)
        except Exception:
            logger.exception("Unable to read CSV headers for import form dropdowns")
            return

        if not headers:
            return

        set_form_column_field_choices(form=self, field_names=_COLUMN_FIELDS, headers=headers)

        question_field_names = tuple(
            f"{spec.field_name}_column" for spec in MembershipRequestForm.all_question_specs()
        )
        set_form_column_field_choices(form=self, field_names=question_field_names, headers=headers)


class MembershipCSVConfirmImportForm(ConfirmImportForm):
    membership_type = forms.ModelChoiceField(
        queryset=_USER_IMPORT_MEMBERSHIP_TYPES,
        required=True,
        widget=forms.HiddenInput,
    )

    email_column = forms.CharField(required=False, widget=forms.HiddenInput)
    name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    active_member_column = forms.CharField(required=False, widget=forms.HiddenInput)
    membership_start_date_column = forms.CharField(required=False, widget=forms.HiddenInput)
    membership_end_date_column = forms.CharField(required=False, widget=forms.HiddenInput)
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
    decision = fields.Field(attribute="decision", column_name="Decision", readonly=True)
    decision_reason = fields.Field(attribute="decision_reason", column_name="Decision Reason", readonly=True)

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
        membership_end_date_column: str = "",
        committee_notes_column: str = "",
        membership_type_column: str = "",
        question_column_overrides: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._membership_type = membership_type
        self._actor_username = actor_username

        self._enable_name_matching = bool(enable_name_matching)

        self._column_overrides: dict[str, str] = {
            "email": email_column,
            "name": name_column,
            "active_member": active_member_column,
            "membership_start_date": membership_start_date_column,
            "membership_end_date": membership_end_date_column,
            "committee_notes": committee_notes_column,
            "membership_type": membership_type_column,
        }
        self._question_column_overrides = question_column_overrides or {}

        self._headers: list[str] = []
        self._resolved_headers: dict[str, str | None] = {}

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

        self._import_batch_id: uuid.UUID | None = None

    @override
    def import_data(self, dataset: Dataset, dry_run: bool = False, raise_errors: bool = False, **kwargs: Any) -> Any:
        if not dry_run:
            self._import_batch_id = uuid.uuid4()
        try:
            rows_total = len(dataset)
        except Exception:
            rows_total = 0

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
                    try:
                        totals = dict(getattr(result, "totals", {}) or {})
                    except Exception:
                        totals = {}

                    rows_applied = (
                        int(totals.get("new", 0))
                        + int(totals.get("update", 0))
                        + int(totals.get("delete", 0))
                    )
                    rows_failed = int(totals.get("error", 0)) + int(totals.get("invalid", 0))

                correlation_id = str(self._import_batch_id)
                logger.info(
                    (
                        "event=astra.membership.csv_import.batch_applied "
                        f"component=membership outcome={outcome} "
                        f"correlation_id={correlation_id} batch_id={self._import_batch_id} "
                        f"rows_total={rows_total} rows_applied={rows_applied} rows_failed={rows_failed}"
                    ),
                    extra={
                        "event": "astra.membership.csv_import.batch_applied",
                        "component": "membership",
                        "outcome": outcome,
                        "correlation_id": correlation_id,
                        "batch_id": self._import_batch_id,
                        "rows_total": rows_total,
                        "rows_applied": rows_applied,
                        "rows_failed": rows_failed,
                    },
                )
            self._import_batch_id = None

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
            row_result = super().import_row(row, instance_loader, **kwargs)

            instance: MembershipRequest | None = None
            try:
                maybe_instance = row_result.instance
            except AttributeError:
                maybe_instance = None
            if isinstance(maybe_instance, MembershipRequest):
                instance = maybe_instance

            if instance is None:
                instance = MembershipRequest()
                row_result.instance = instance

            self._populate_preview_fields(instance, row)
            return row_result
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
                row_decision = self._decision_for_row(row)
                decision = row_decision.decision
                reason = row_decision.reason
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
        header_by_norm = {norm_csv_header(h): h for h in headers if h}

        self._resolved_headers = {
            "email": resolve_column_header(
                "email",
                headers,
                header_by_norm,
                self._column_overrides,
                "email",
                "emailaddress",
                "mail",
            ),
            "name": resolve_column_header(
                "name",
                headers,
                header_by_norm,
                self._column_overrides,
                "name",
                "fullname",
            ),
            "active_member": resolve_column_header(
                "active_member",
                headers,
                header_by_norm,
                self._column_overrides,
                "activemember",
                "active",
                "status",
            ),
            "membership_start_date": resolve_column_header(
                "membership_start_date",
                headers,
                header_by_norm,
                self._column_overrides,
                "membershipstartdate",
                "startdate",
            ),
            "membership_end_date": resolve_column_header(
                "membership_end_date",
                headers,
                header_by_norm,
                self._column_overrides,
                "membershipenddate",
                "enddate",
                "membershipexpirydate",
                "membershipexpirationdate",
                "expirydate",
                "expirationdate",
            ),
            "committee_notes": resolve_column_header(
                "committee_notes",
                headers,
                header_by_norm,
                self._column_overrides,
                "committeenotes",
                "committeenote",
                "fasstatusnote",
                "note",
                "notes",
            ),
            "membership_type": resolve_column_header(
                "membership_type",
                headers,
                header_by_norm,
                self._column_overrides,
                "membershiptype",
                "type",
            ),
        }

        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        self._question_header_by_name = {}
        for spec in MembershipRequestForm.question_specs_for_membership_type(membership_type):
            key = f"{spec.field_name}_column"
            resolved = resolve_column_header(
                key,
                headers,
                header_by_norm,
                self._question_column_overrides,
                spec.name,
                spec.field_name,
                spec.field_name.removeprefix("q_"),
            )
            self._question_header_by_name[spec.name] = resolved

        if self._resolved_headers.get("email") is None:
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
            email = normalize_csv_email(user.email)
            username = _normalize_str(user.username)
            if not email or not username:
                continue
            self._email_to_usernames.setdefault(email, set()).add(username)

            if self._enable_name_matching:
                key = normalize_csv_name(user.full_name)
                if key:
                    self._name_to_usernames.setdefault(key, set()).add(username)

        logger.info(
            "Membership CSV import: headers=%d email_header=%r name_header=%r active_header=%r start_header=%r end_header=%r note_header=%r type_header=%r freeipa_users=%d unique_emails=%d",
            len(headers),
            self._resolved_headers.get("email"),
            self._resolved_headers.get("name"),
            self._resolved_headers.get("active_member"),
            self._resolved_headers.get("membership_start_date"),
            self._resolved_headers.get("membership_end_date"),
            self._resolved_headers.get("committee_notes"),
            self._resolved_headers.get("membership_type"),
            len(users),
            len(self._email_to_usernames),
        )

        question_columns = {name: header for name, header in self._question_header_by_name.items() if header}
        if question_columns:
            logger.info(
                "Membership CSV import: question_columns=%r",
                question_columns,
            )

        if self._resolved_headers.get("active_member") is None:
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
            if usernames:
                self._email_lookup_cache[normalized] = usernames
                # Email is PII; keep this at DEBUG level.
                logger.debug(
                    "Membership CSV import: email lookup via directory email=%r matches=%r",
                    normalized,
                    sorted(usernames),
                )
                return usernames

            # Fall back to a targeted lookup if the directory listing missed
            # the email (stale cache or incomplete attribute set).
            user = FreeIPAUser.find_by_email(normalized)
            usernames = {user.username} if user and user.username else set()
            self._email_lookup_cache[normalized] = usernames
            # Email is PII; keep this at DEBUG level.
            logger.debug(
                "Membership CSV import: email lookup via directory+find_by_email email=%r match=%r",
                normalized,
                next(iter(usernames), ""),
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

        key = normalize_csv_name(name)
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

    def _row_value(self, row: Any, logical_name: str) -> str:
        header = self._resolved_headers.get(logical_name)
        if not header:
            return ""
        try:
            return _normalize_str(row.get(header, ""))
        except AttributeError:
            return ""

    def _row_email(self, row: Any) -> str:
        return normalize_csv_email(self._row_value(row, "email"))

    def _row_name(self, row: Any) -> str:
        return self._row_value(row, "name")

    def _row_is_active(self, row: Any) -> bool:
        # Some membership exports omit an explicit active/status column and
        # implicitly represent only active members. In that case, treat rows as
        # eligible rather than skipping everything.
        if self._resolved_headers.get("active_member") is None:
            return True
        return parse_csv_bool(self._row_value(row, "active_member"))

    def _row_approved_at(self, row: Any) -> datetime.datetime | None:
        if self._resolved_headers.get("membership_start_date") is None:
            return None
        start_date = parse_csv_date(self._row_value(row, "membership_start_date"))
        if start_date is None:
            return None
        return datetime.datetime.combine(start_date, datetime.time(0, 0, 0), tzinfo=datetime.UTC)

    def _row_expires_at(self, row: Any) -> datetime.datetime | None:
        if self._resolved_headers.get("membership_end_date") is None:
            return None
        end_date = parse_csv_date(self._row_value(row, "membership_end_date"))
        if end_date is None:
            return None
        return datetime.datetime.combine(end_date, datetime.time(0, 0, 0), tzinfo=datetime.UTC)

    def _row_has_expiry_value(self, row: Any) -> bool:
        return bool(self._row_value(row, "membership_end_date"))

    def _row_note(self, row: Any) -> str:
        return self._row_value(row, "committee_notes")

    def _row_csv_membership_type(self, row: Any) -> str:
        return self._row_value(row, "membership_type")

    def _decision_for_row(self, row: Any) -> _RowDecision:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        start_at = self._row_approved_at(row)
        end_at = self._row_expires_at(row)
        row_note = self._row_note(row)
        responses = self._row_responses(row)

        def row_decision(*, decision: str, reason: str, username: str = "", match_method: str = "") -> _RowDecision:
            return _RowDecision(
                decision=decision,
                reason=reason,
                username=username,
                match_method=match_method,
                start_at=start_at,
                end_at=end_at,
                row_note=row_note,
                responses=responses,
            )

        email = self._row_email(row)
        if not email:
            return row_decision(decision="SKIP", reason="Missing Email")

        username, match_method, reason = self._match_username_for_row(row)
        if not username:
            return row_decision(decision="SKIP", reason=reason or "No match", match_method=match_method)

        if not self._row_is_active(row):
            return row_decision(
                decision="SKIP",
                reason="Not an Active Member",
                username=username,
                match_method=match_method,
            )

        raw_type = self._row_csv_membership_type(row)
        if raw_type and not _membership_type_matches(raw_type, membership_type):
            return row_decision(
                decision="SKIP",
                reason=f"CSV type '{raw_type}' does not match selected '{membership_type.code}'",
                username=username,
                match_method=match_method,
            )

        missing = missing_required_agreements_for_user_in_group(username, membership_type.group_cn)
        if missing:
            return row_decision(
                decision="SKIP",
                reason=f"Missing required agreements for '{membership_type.group_cn}': {', '.join(missing)}",
                username=username,
                match_method=match_method,
            )

        if self._row_has_expiry_value(row) and end_at is None:
            return row_decision(
                decision="SKIP",
                reason="Invalid membership end date",
                username=username,
                match_method=match_method,
            )

        if end_at is not None:
            effective_start_at = start_at or timezone.now().astimezone(datetime.UTC)
            if end_at <= effective_start_at:
                return row_decision(
                    decision="SKIP",
                    reason="Membership end date must be after start date",
                    username=username,
                    match_method=match_method,
                )

        existing_request = (
            MembershipRequest.objects.filter(
                requested_username=username,
                membership_type=membership_type,
            )
            .only("responses", "requested_at")
            .order_by("-requested_at", "-pk")
            .first()
        )
        note_exists = False
        if row_note:
            note_exists = Note.objects.filter(
                membership_request__requested_username=username,
                membership_request__membership_type=membership_type,
                content=f"[Import] {row_note}",
            ).exists()

        responses_have_new = False
        if responses:
            existing_responses = []
            if existing_request is not None and isinstance(existing_request.responses, list):
                existing_responses = existing_request.responses
            responses_have_new = any(item not in existing_responses for item in responses)

        has_updates = (bool(row_note) and not note_exists) or responses_have_new

        open_request = (
            MembershipRequest.objects.filter(
                requested_username=username,
                membership_type=membership_type,
                status__in=[
                    MembershipRequest.Status.pending,
                    MembershipRequest.Status.on_hold,
                ],
            )
            .only("status")
            .first()
        )

        if open_request is not None and open_request.status == MembershipRequest.Status.on_hold:
            if has_updates:
                return row_decision(
                    decision="IMPORT",
                    reason="Request on hold, will ignore",
                    username=username,
                    match_method=match_method,
                )
            return row_decision(
                decision="SKIP",
                reason="Request on hold, will ignore",
                username=username,
                match_method=match_method,
            )

        if open_request is not None and open_request.status == MembershipRequest.Status.pending:
            return row_decision(
                decision="IMPORT",
                reason="Active request, will be accepted",
                username=username,
                match_method=match_method,
            )

        existing_membership = (
            Membership.objects.active()
            .filter(
                target_username=username,
                membership_type=membership_type,
            )
            .only("created_at")
            .first()
        )
        if existing_membership is not None and start_at is not None:
            if existing_membership.created_at == start_at and not has_updates:
                return row_decision(
                    decision="SKIP",
                    reason="Already up-to-date",
                    username=username,
                    match_method=match_method,
                )
            if existing_membership.created_at != start_at:
                return row_decision(
                    decision="IMPORT",
                    reason="Active membership, updating start date",
                    username=username,
                    match_method=match_method,
                )

        if existing_membership is not None and start_at is None:
            if not has_updates:
                return row_decision(
                    decision="SKIP",
                    reason="Already up-to-date",
                    username=username,
                    match_method=match_method,
                )
            return row_decision(
                decision="IMPORT",
                reason="Active membership, importing updates",
                username=username,
                match_method=match_method,
            )

        if existing_membership is not None and has_updates:
            return row_decision(
                decision="IMPORT",
                reason="Active membership, importing updates",
                username=username,
                match_method=match_method,
            )

        if existing_membership is not None:
            return row_decision(
                decision="IMPORT",
                reason="Active membership, updating start date",
                username=username,
                match_method=match_method,
            )

        return row_decision(
            decision="IMPORT",
            reason="New request will be created",
            username=username,
            match_method=match_method,
        )

    def _populate_preview_fields(self, instance: MembershipRequest, row: Any) -> None:
        instance._csv_name = self._row_name(row)
        instance._csv_email = self._row_email(row)
        instance._csv_active_member = self._row_value(row, "active_member")
        instance._csv_membership_start_date = self._row_value(row, "membership_start_date")
        instance._csv_membership_type = self._row_csv_membership_type(row)
        instance._csv_committee_notes = self._row_note(row)

        row_decision = self._decision_for_row(row)
        instance._matched_username = row_decision.username
        instance.decision = row_decision.decision
        instance.decision_reason = row_decision.reason

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
            used_norms.add(norm_csv_header(header))
            try:
                value = _normalize_str(row.get(header, ""))
            except AttributeError:
                value = ""
            if value or spec.required:
                question_responses.append({spec.name: value})

        reserved_norms = {
            norm_csv_header(self._resolved_headers["email"]) if self._resolved_headers.get("email") else "",
            norm_csv_header(self._resolved_headers["name"]) if self._resolved_headers.get("name") else "",
            norm_csv_header(self._resolved_headers["active_member"]) if self._resolved_headers.get("active_member") else "",
            norm_csv_header(self._resolved_headers["membership_start_date"])
            if self._resolved_headers.get("membership_start_date")
            else "",
            norm_csv_header(self._resolved_headers["membership_end_date"])
            if self._resolved_headers.get("membership_end_date")
            else "",
            norm_csv_header(self._resolved_headers["committee_notes"])
            if self._resolved_headers.get("committee_notes")
            else "",
            norm_csv_header(self._resolved_headers["membership_type"])
            if self._resolved_headers.get("membership_type")
            else "",
        }

        reserved_norms |= used_norms

        responses: list[dict[str, str]] = list(question_responses)
        for header in self._headers:
            if not header or norm_csv_header(header) in reserved_norms:
                continue
            try:
                value = _normalize_str(row.get(header, ""))
            except AttributeError:
                value = ""
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
            try:
                item[header] = _normalize_str(row.get(header, ""))
            except AttributeError:
                item[header] = ""
        item["reason"] = reason
        self._unmatched.append(item)

    @override
    def before_import_row(self, row: Any, **kwargs: Any) -> None:
        row_number = kwargs.get("row_number")
        email = self._row_email(row)
        if not email:
            # Treat blank/empty lines as no-ops.
            return

        row_decision = self._decision_for_row(row)
        usernames = {row_decision.username} if row_decision.username else set()
        if isinstance(row_number, int) and row_number <= 50:
            # Email is PII; keep this at DEBUG level.
            logger.debug(
                "Membership CSV import: row=%d email=%r usernames=%r",
                row_number,
                email,
                sorted(usernames),
            )

        if row_decision.username and row_decision.match_method == "email":
            self._matched_by_email += 1
        elif row_decision.username and row_decision.match_method == "name":
            self._matched_by_name += 1

        if not row_decision.username:
            self._record_unmatched(row=row, reason=row_decision.reason or "No match")
            return

        # Don't block the import preview/confirm flow for per-row business rules.
        # These rows will be skipped during import.

    @override
    def skip_row(self, instance: Any, original: Any, row: Any, import_validation_errors: Any = None) -> bool:
        preview_instance = instance if isinstance(instance, MembershipRequest) else MembershipRequest()
        self._populate_preview_fields(preview_instance, row)

        row_decision = self._decision_for_row(row)
        self._decision_counts[row_decision.decision] = self._decision_counts.get(row_decision.decision, 0) + 1
        if row_decision.decision != "IMPORT" and row_decision.reason:
            self._skip_reason_counts[row_decision.reason] = self._skip_reason_counts.get(row_decision.reason, 0) + 1

        row_number = getattr(row, "number", None)
        # Email is PII; keep row-level decisions at DEBUG level.
        if isinstance(row_number, int) and row_number <= 50:
            logger.debug(
                "Membership CSV import: decision row=%d decision=%s reason=%r",
                row_number,
                row_decision.decision,
                row_decision.reason,
            )

        return row_decision.decision != "IMPORT"

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

        # import_instance is called even for rows that will later be skipped.
        # Only set required fields for rows we intend to validate/save.
        row_decision = self._decision_for_row(row)
        if row_decision.decision != "IMPORT":
            return

        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        username = row_decision.username
        if not username:
            return

        responses = row_decision.responses

        existing_open = (
            MembershipRequest.objects.filter(
                requested_username=username,
                membership_type=membership_type,
                status__in=[
                    MembershipRequest.Status.pending,
                    MembershipRequest.Status.on_hold,
                ],
            )
            # When re-using an existing request, we must carry over requested_at.
            # Otherwise, the import-export save() call will issue an UPDATE with
            # requested_at=NULL (because this Resource starts from a fresh instance
            # and then assigns pk).
            .only("pk", "responses", "requested_at", "status")
            .first()
        )
        instance._csv_created_request = existing_open is None
        instance._csv_on_hold_request = (
            existing_open is not None
            and existing_open.status == MembershipRequest.Status.on_hold
        )
        if existing_open is not None:
            instance.pk = existing_open.pk
            instance.requested_at = existing_open.requested_at
            instance.status = existing_open.status

        merged_responses: list[dict[str, str]] = []
        if existing_open is not None and isinstance(existing_open.responses, list):
            merged_responses.extend(existing_open.responses)
        for item in responses:
            if item not in merged_responses:
                merged_responses.append(item)

        instance.requested_username = username
        instance.requested_organization = None
        instance.requested_organization_code = ""
        instance.requested_organization_name = ""
        instance.membership_type = membership_type
        if existing_open is None:
            instance.status = MembershipRequest.Status.pending
        instance.responses = merged_responses

    def _previous_expires_at(
        self,
        *,
        username: str,
        approved_at: datetime.datetime,
    ) -> datetime.datetime | None:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        existing_membership = (
            Membership.objects.filter(
                target_username=username,
                membership_type=membership_type,
            )
            .only("expires_at")
            .first()
        )
        if (
            existing_membership is None
            or existing_membership.expires_at is None
            or existing_membership.expires_at <= approved_at
        ):
            return None
        return existing_membership.expires_at

    def _apply_row(
        self,
        *,
        instance: MembershipRequest,
        row_number: int | None,
        email: str,
        username: str,
        start_at: datetime.datetime,
        end_at: datetime.datetime | None,
        decided_at: datetime.datetime,
        row_decision: _RowDecision,
        previous_expires_at: datetime.datetime | None,
    ) -> None:
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

        existing_log_ids: set[int] = set()
        if self._import_batch_id is not None:
            existing_log_ids = set(
                MembershipLog.objects.filter(membership_request=instance).values_list("pk", flat=True)
            )

        # `import_instance()` should have precomputed merged responses before
        # save hooks run. Keep this defensive fallback in case a future
        # import-export version changes call ordering.
        if row_decision.responses and not instance.responses:
            instance.responses = row_decision.responses

        if instance._csv_on_hold_request:
            if row_decision.row_note:
                add_note(
                    membership_request=instance,
                    username=self._actor_username,
                    content=f"[Import] {row_decision.row_note}",
                )
            logger.info(
                "Membership CSV import: apply ignored (on-hold) row=%s email=%r username=%r membership_type=%s",
                row_number,
                email,
                username,
                instance.membership_type_id,
            )
            return

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

            # If we're reusing an existing pending request (i.e. the user applied
            # via the UI), approve via the normal workflow including the approval
            # email. For importer-created requests, keep the historic behavior of
            # *not* emailing (operators are usually bulk-importing a roster).
            send_approved_email = not instance._csv_created_request

            approve_membership_request(
                membership_request=instance,
                actor_username=self._actor_username,
                send_approved_email=send_approved_email,
                decided_at=decided_at,
            )

            # requested_at is auto_now_add, so Django overwrites it on create.
            # For CSV imports we want request time to reflect the CSV start date
            # (or now if none was provided).
            MembershipRequest.objects.filter(pk=instance.pk).update(requested_at=start_at)

            membership_qs = Membership.objects.filter(
                target_username=instance.requested_username,
                membership_type=instance.membership_type,
            )
            # `created_at` is auto_now_add; backfill the membership start date from the CSV.
            membership_qs.update(created_at=start_at)

            if end_at is not None and end_at > start_at:
                membership_qs.update(expires_at=end_at)
            elif previous_expires_at is not None:
                membership_qs.update(expires_at=previous_expires_at)

            if self._import_batch_id is not None:
                imported_logs = MembershipLog.objects.filter(membership_request=instance)
                if existing_log_ids:
                    imported_logs = imported_logs.exclude(pk__in=existing_log_ids)
                imported_logs.update(import_batch_id=self._import_batch_id)

            # Only record the import note after a fully successful apply. This
            # avoids leaving misleading "[Import]" notes behind when approval
            # fails (e.g. FreeIPA group add failure).
            if row_decision.row_note:
                add_note(
                    membership_request=instance,
                    username=self._actor_username,
                    content=f"[Import] {row_decision.row_note}",
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
    def after_save_instance(self, instance: MembershipRequest, row: Any, **kwargs: Any) -> None:
        super().after_save_instance(instance, row, **kwargs)

        if bool(kwargs.get("dry_run")):
            return

        row_number = kwargs.get("row_number")
        email = self._row_email(row)
        row_decision = self._decision_for_row(row)
        if row_decision.decision != "IMPORT":
            return

        username = row_decision.username or instance.requested_username
        start_at = row_decision.start_at
        end_at = row_decision.end_at
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
        previous_expires_at = self._previous_expires_at(username=username, approved_at=now)

        self._apply_row(
            instance=instance,
            row_number=row_number if isinstance(row_number, int) else None,
            email=email,
            username=username,
            start_at=start_at,
            end_at=end_at,
            decided_at=decided_at,
            row_decision=row_decision,
            previous_expires_at=previous_expires_at,
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
            row_decision = self._decision_for_row(row)
            decision = row_decision.decision
            reason = row_decision.reason
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

        headers = [h for h in self._headers if h]
        reason_header = "reason"
        if any(norm_csv_header(h) == "reason" for h in headers):
            # Avoid clobbering an existing input column.
            reason_header = "unmatched_reason"

        export_rows: list[dict[str, str]] = []
        export_headers = [*headers, reason_header]
        for item in self._unmatched:
            export_item = {
                header: sanitize_csv_cell(item.get(header, ""))
                for header in headers
            }
            export_item[reason_header] = item.get("reason", "")
            export_rows.append(export_item)

        unmatched_dataset = Dataset()
        unmatched_dataset.headers = export_headers
        for row in export_rows:
            unmatched_dataset.append([row.get(header, "") for header in export_headers])

        attach_unmatched_csv_to_result(
            result,
            unmatched_dataset,
            "membership-import-unmatched",
            "admin:core_membershipcsvimportlink_download_unmatched",
        )
