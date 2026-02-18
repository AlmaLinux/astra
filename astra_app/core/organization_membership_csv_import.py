import datetime
import logging
from dataclasses import dataclass
from typing import Any, override

from django import forms
from django.utils import timezone
from import_export import fields, resources
from import_export.forms import ConfirmImportForm, ImportForm
from tablib import Dataset

from core.csv_import_utils import (
    attach_unmatched_csv_to_result,
    extract_csv_headers_from_uploaded_file,
    norm_csv_header,
    parse_csv_date,
    resolve_column_header,
    sanitize_csv_cell,
    set_form_column_field_choices,
)
from core.forms_membership import MembershipRequestForm
from core.membership_notes import add_note
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Organization
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class _ColumnSpec:
    field_name: str
    logical_name: str
    help_text: str
    required: bool
    display_name: str


_COLUMN_SPECS: tuple[_ColumnSpec, ...] = (
    # Either identifier can match an organization row. Keep a shared display
    # label and dedupe in required_organization_membership_csv_columns().
    _ColumnSpec(
        field_name="organization_id_column",
        logical_name="organization_id",
        help_text="Optional: CSV column containing organization ID.",
        required=True,
        display_name="organization_id OR organization_name",
    ),
    _ColumnSpec(
        field_name="organization_name_column",
        logical_name="organization_name",
        help_text="Optional: CSV column containing organization name.",
        required=True,
        display_name="organization_id OR organization_name",
    ),
    _ColumnSpec(
        field_name="membership_start_date_column",
        logical_name="membership_start_date",
        help_text="Optional: CSV start date column used for audit backfill only.",
        required=False,
        display_name="membership_start_date",
    ),
    _ColumnSpec(
        field_name="membership_end_date_column",
        logical_name="membership_end_date",
        help_text="Optional: CSV end date column used as explicit expiry.",
        required=False,
        display_name="membership_end_date",
    ),
    _ColumnSpec(
        field_name="committee_notes_column",
        logical_name="committee_notes",
        help_text="Optional: CSV committee notes column.",
        required=False,
        display_name="committee_notes",
    ),
)

_ORG_MEMBERSHIP_COLUMN_FIELDS = tuple(spec.field_name for spec in _COLUMN_SPECS)


def organization_membership_csv_column_specs() -> tuple[_ColumnSpec, ...]:
    return _COLUMN_SPECS


def _column_help_text(field_name: str) -> str:
    for spec in _COLUMN_SPECS:
        if spec.field_name == field_name:
            return spec.help_text
    raise ValueError(f"Unknown organization membership CSV field: {field_name}")


def _display_names_for_specs(*, required: bool) -> tuple[str, ...]:
    names = [
        spec.display_name
        for spec in _COLUMN_SPECS
        if spec.required is required and spec.display_name
    ]
    # Preserve declaration order and collapse duplicate group labels.
    return tuple(dict.fromkeys(names))


def required_organization_membership_csv_columns() -> tuple[str, ...]:
    return _display_names_for_specs(required=True)


def optional_organization_membership_csv_columns() -> tuple[str, ...]:
    return _display_names_for_specs(required=False)


class OrganizationMembershipCSVImportForm(ImportForm):
    membership_type = forms.ModelChoiceField(
        queryset=MembershipType.objects.filter(enabled=True, category__is_organization=True).order_by(
            "category__sort_order",
            "sort_order",
            "name",
        ),
        required=True,
        help_text="Membership type to grant for all imported rows.",
    )

    organization_id_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text=_column_help_text("organization_id_column"),
    )
    organization_name_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text=_column_help_text("organization_name_column"),
    )
    membership_start_date_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text=_column_help_text("membership_start_date_column"),
    )
    membership_end_date_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text=_column_help_text("membership_end_date_column"),
    )
    committee_notes_column = forms.ChoiceField(
        required=False,
        choices=[("", "Auto-detect")],
        help_text=_column_help_text("committee_notes_column"),
    )

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        format_field = self.fields.get("format")
        if format_field is not None:
            format_choices = list(format_field.choices)
            if len(format_choices) == 1:
                format_field.initial = format_choices[0][0]
                format_field.widget = forms.HiddenInput()

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
                        norm_csv_header(spec.title),
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
            logger.exception("Unable to read CSV headers for organization membership import form")
            return

        if not headers:
            return

        set_form_column_field_choices(
            form=self,
            field_names=_ORG_MEMBERSHIP_COLUMN_FIELDS,
            headers=headers,
        )

        question_field_names = tuple(
            f"{spec.field_name}_column" for spec in MembershipRequestForm.all_question_specs()
        )
        set_form_column_field_choices(
            form=self,
            field_names=question_field_names,
            headers=headers,
        )


class OrganizationMembershipCSVConfirmImportForm(ConfirmImportForm):
    membership_type = forms.ModelChoiceField(
        queryset=MembershipType.objects.filter(enabled=True, category__is_organization=True).order_by(
            "category__sort_order",
            "sort_order",
            "name",
        ),
        required=True,
        widget=forms.HiddenInput,
    )

    organization_id_column = forms.CharField(required=False, widget=forms.HiddenInput)
    organization_name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    membership_start_date_column = forms.CharField(required=False, widget=forms.HiddenInput)
    membership_end_date_column = forms.CharField(required=False, widget=forms.HiddenInput)
    committee_notes_column = forms.CharField(required=False, widget=forms.HiddenInput)
    selected_row_numbers = forms.CharField(required=False, widget=forms.HiddenInput)

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        for spec in MembershipRequestForm.all_question_specs():
            field_name = f"{spec.field_name}_column"
            if field_name in self.fields:
                continue
            self.fields[field_name] = forms.CharField(required=False, widget=forms.HiddenInput)


class OrganizationMembershipCSVImportResource(resources.ModelResource):
    organization_id = fields.Field(attribute="csv_organization_id", column_name="Organization ID", readonly=True)
    organization_name = fields.Field(attribute="csv_organization_name", column_name="Organization Name", readonly=True)
    membership_start_date = fields.Field(
        attribute="csv_membership_start_date",
        column_name="Membership Start Date",
        readonly=True,
    )
    membership_end_date = fields.Field(
        attribute="csv_membership_end_date",
        column_name="Membership End Date",
        readonly=True,
    )
    committee_notes = fields.Field(attribute="csv_committee_notes", column_name="Committee Notes", readonly=True)
    matched_organization = fields.Field(attribute="matched_organization", column_name="Matched Organization", readonly=True)
    decision = fields.Field(attribute="decision", column_name="Decision", readonly=True)
    decision_reason = fields.Field(attribute="decision_reason", column_name="Decision Reason", readonly=True)

    class Meta:
        model = Organization
        import_id_fields = ()
        fields = (
            "organization_id",
            "organization_name",
            "membership_start_date",
            "membership_end_date",
            "committee_notes",
            "matched_organization",
            "decision",
            "decision_reason",
        )
        use_transactions = True
        use_bulk = False

    def __init__(
        self,
        *,
        membership_type: MembershipType | None = None,
        actor_username: str = "",
        organization_id_column: str = "",
        organization_name_column: str = "",
        membership_start_date_column: str = "",
        membership_end_date_column: str = "",
        committee_notes_column: str = "",
        question_column_overrides: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._membership_type = membership_type
        self._actor_username = actor_username

        self._column_overrides = {
            "organization_id": organization_id_column,
            "organization_name": organization_name_column,
            "membership_start_date": membership_start_date_column,
            "membership_end_date": membership_end_date_column,
            "committee_notes": committee_notes_column,
        }
        self._question_column_overrides = question_column_overrides or {}

        self._resolved_headers: dict[str, str | None] = {}
        self._question_header_by_name: dict[str, str | None] = {}
        self._organizations_by_id: dict[int, Organization] = {}
        self._organizations_by_name: dict[str, list[Organization]] = {}
        self._unmatched: list[dict[str, str]] = []
        self._decision_counts: dict[str, int] = {}
        self._skip_reason_counts: dict[str, int] = {}

    @override
    def before_import(self, dataset: Dataset, **kwargs: Any) -> None:
        if self._membership_type is None:
            raise ValueError("membership_type is required")

        headers = list(dataset.headers or [])
        if not headers:
            raise ValueError("CSV has no headers")

        header_by_norm = {norm_csv_header(header): header for header in headers if header}

        self._resolved_headers = {
            "organization_id": resolve_column_header(
                "organization_id",
                headers,
                header_by_norm,
                self._column_overrides,
                "organizationid",
                "orgid",
                "id",
            ),
            "organization_name": resolve_column_header(
                "organization_name",
                headers,
                header_by_norm,
                self._column_overrides,
                "organizationname",
                "orgname",
                "name",
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
                "note",
                "notes",
            ),
        }

        if self._resolved_headers["organization_id"] is None and self._resolved_headers["organization_name"] is None:
            raise ValueError("CSV must include organization_id and/or organization_name columns")

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
                spec.title,
                spec.name,
                spec.field_name,
                spec.field_name.removeprefix("q_"),
            )
            self._question_header_by_name[spec.name] = resolved

        self._organizations_by_id = {}
        self._organizations_by_name = {}
        for organization in Organization.objects.all().only("id", "name"):
            self._organizations_by_id[organization.pk] = organization
            key = str(organization.name).strip().casefold()
            if key:
                self._organizations_by_name.setdefault(key, []).append(organization)

        self._unmatched = []
        self._decision_counts = {}
        self._skip_reason_counts = {}

    def _row_value(self, row: Any, logical_name: str) -> str:
        header = self._resolved_headers.get(logical_name)
        if not header:
            return ""
        try:
            return _normalize_str(row.get(header, ""))
        except AttributeError:
            return ""

    def _row_start_at(self, row: Any) -> datetime.datetime | None:
        start_date = parse_csv_date(self._row_value(row, "membership_start_date"))
        if start_date is None:
            return None
        return datetime.datetime.combine(start_date, datetime.time(0, 0, 0), tzinfo=datetime.UTC)

    def _row_end_at(self, row: Any) -> datetime.datetime | None:
        end_date = parse_csv_date(self._row_value(row, "membership_end_date"))
        if end_date is None:
            return None
        return datetime.datetime.combine(end_date, datetime.time(0, 0, 0), tzinfo=datetime.UTC)

    def _row_has_start_value(self, row: Any) -> bool:
        return bool(self._row_value(row, "membership_start_date"))

    def _row_has_end_value(self, row: Any) -> bool:
        return bool(self._row_value(row, "membership_end_date"))

    def _match_organization(self, row: Any) -> tuple[Organization | None, str]:
        raw_id = self._row_value(row, "organization_id")
        raw_name = self._row_value(row, "organization_name")

        if not raw_id and not raw_name:
            return None, "Missing organization identifier"

        parsed_id: int | None = None
        if raw_id:
            try:
                parsed_id = int(raw_id)
            except ValueError:
                if not raw_name:
                    return None, "Invalid organization ID"

        if parsed_id is not None:
            organization = self._organizations_by_id.get(parsed_id)
            if organization is not None:
                return organization, "Matched by organization ID"
            if not raw_name:
                return None, "Organization not found"

        if raw_name:
            matches = self._organizations_by_name.get(raw_name.strip().casefold(), [])
            if len(matches) == 1:
                return matches[0], "Matched by organization name"
            if len(matches) > 1:
                return None, f"Ambiguous organization name ({len(matches)} matches)"

        return None, "Organization not found"

    def _row_responses(self, row: Any) -> list[dict[str, str]]:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        responses: list[dict[str, str]] = []
        for spec in MembershipRequestForm.question_specs_for_membership_type(membership_type):
            header = self._question_header_by_name.get(spec.name)
            if not header:
                continue
            try:
                value = _normalize_str(row.get(header, ""))
            except AttributeError:
                value = ""
            if value or spec.required:
                responses.append({spec.name: value})
        return responses

    def _decision_for_row(
        self,
        row: Any,
    ) -> tuple[str, str, Organization | None, str, list[dict[str, str]], datetime.datetime | None, datetime.datetime | None]:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        responses = self._row_responses(row)

        if not membership_type.category.is_organization:
            return "SKIP", "Membership type is not valid for organizations", None, "", responses, None, None

        organization, match_reason = self._match_organization(row)
        if organization is None:
            return "SKIP", match_reason, None, "", responses, None, None

        start_at = self._row_start_at(row)
        if self._row_has_start_value(row) and start_at is None:
            return "SKIP", "Invalid membership start date", None, "", responses, None, None

        end_at = self._row_end_at(row)
        if self._row_has_end_value(row) and end_at is None:
            return "SKIP", "Invalid membership end date", None, "", responses, None, None

        if start_at is not None and end_at is not None and end_at <= start_at:
            return "SKIP", "Membership end date must be after start date", None, "", responses, None, None

        row_note = self._row_value(row, "committee_notes")
        return "IMPORT", "Ready to import", organization, row_note, responses, start_at, end_at

    def _populate_preview_fields(self, instance: Organization, row: Any) -> None:
        decision, reason, organization, row_note, _responses, _start_at, _end_at = self._decision_for_row(row)
        setattr(instance, "csv_organization_id", self._row_value(row, "organization_id"))
        setattr(instance, "csv_organization_name", self._row_value(row, "organization_name"))
        setattr(instance, "csv_membership_start_date", self._row_value(row, "membership_start_date"))
        setattr(instance, "csv_membership_end_date", self._row_value(row, "membership_end_date"))
        setattr(instance, "csv_committee_notes", row_note)
        setattr(instance, "matched_organization", organization.name if organization is not None else "")
        setattr(instance, "decision", decision)
        setattr(instance, "decision_reason", reason)

    def _record_unmatched(self, *, row: Any, row_number: int, reason: str) -> None:
        self._unmatched.append(
            {
                "row_number": str(row_number),
                "organization_id": self._row_value(row, "organization_id"),
                "organization_name": self._row_value(row, "organization_name"),
                "skip_reason": reason,
            }
        )

    @override
    def before_import_row(self, row: Any, **kwargs: Any) -> None:
        decision, reason, _organization, _row_note, _responses, _start_at, _end_at = self._decision_for_row(row)
        if decision != "IMPORT":
            row_number = kwargs.get("row_number")
            if isinstance(row_number, int):
                self._record_unmatched(row=row, row_number=row_number, reason=reason)

    @override
    def import_row(self, row: Any, instance_loader: Any, **kwargs: Any) -> Any:
        row_result = super().import_row(row, instance_loader, **kwargs)
        instance = getattr(row_result, "instance", None)
        if instance is None:
            instance = Organization()
            row_result.instance = instance
        self._populate_preview_fields(instance, row)
        return row_result

    @override
    def skip_row(self, instance: Any, original: Any, row: Any, import_validation_errors: Any = None) -> bool:
        self._populate_preview_fields(instance, row)
        decision, reason, _organization, _row_note, _responses, _start_at, _end_at = self._decision_for_row(row)
        self._decision_counts[decision] = self._decision_counts.get(decision, 0) + 1
        if decision != "IMPORT" and reason:
            self._skip_reason_counts[reason] = self._skip_reason_counts.get(reason, 0) + 1
        return decision != "IMPORT"

    def _previous_expires_at(self, *, organization: Organization, approved_at: datetime.datetime) -> datetime.datetime | None:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        existing = (
            Membership.objects.filter(
                target_organization=organization,
                category=membership_type.category,
            )
            .only("expires_at")
            .first()
        )
        if existing is None or existing.expires_at is None or existing.expires_at <= approved_at:
            return None
        return existing.expires_at

    def _apply_row(
        self,
        *,
        organization: Organization,
        row_note: str,
        responses: list[dict[str, str]],
        start_at: datetime.datetime | None,
        end_at: datetime.datetime | None,
        row_number: int,
    ) -> None:
        membership_type = self._membership_type
        if membership_type is None:
            raise ValueError("membership_type is required")

        approved_at = timezone.now().astimezone(datetime.UTC)

        membership_request = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
            decided_at=approved_at,
            decided_by_username=self._actor_username,
            responses=responses,
        )

        if start_at is not None and membership_request.requested_at != start_at:
            MembershipRequest.objects.filter(pk=membership_request.pk).update(requested_at=start_at)

        MembershipLog.create_for_request(
            actor_username=self._actor_username,
            membership_type=membership_type,
            target_organization=organization,
            membership_request=membership_request,
        )

        previous_expires_at = self._previous_expires_at(
            organization=organization,
            approved_at=approved_at,
        )

        # `create_for_approval_at()` persists a MembershipLog row. The model's
        # `save()` hook then triggers `_apply_org_side_effects()`, which calls
        # `Membership.replace_within_category()` to enforce one membership per
        # category for the organization.
        approval_log = MembershipLog.create_for_approval_at(
            actor_username=self._actor_username,
            membership_type=membership_type,
            approved_at=approved_at,
            target_organization=organization,
            previous_expires_at=previous_expires_at,
            membership_request=membership_request,
        )

        if end_at is not None and approval_log.expires_at != end_at:
            MembershipLog.create_for_expiry_change(
                actor_username=self._actor_username,
                membership_type=membership_type,
                target_organization=organization,
                expires_at=end_at,
                membership_request=membership_request,
            )

        membership_qs = Membership.objects.filter(
            target_organization=organization,
            membership_type=membership_type,
        )
        if start_at is not None:
            membership_qs.update(created_at=start_at)

        if row_note:
            add_note(
                membership_request=membership_request,
                username=self._actor_username,
                content=f"[Import] {row_note}",
            )

    @override
    def save_instance(self, instance: Any, is_create: bool, row: Any, **kwargs: Any) -> None:
        if bool(kwargs.get("dry_run")):
            return

        decision, _reason, organization, row_note, responses, start_at, end_at = self._decision_for_row(row)
        if decision != "IMPORT" or organization is None:
            return

        row_number = kwargs.get("row_number")
        if not isinstance(row_number, int):
            row_number = 0

        self._apply_row(
            organization=organization,
            row_note=row_note,
            responses=responses,
            start_at=start_at,
            end_at=end_at,
            row_number=row_number,
        )

        # This importer must not persist preview `Organization` instances.
        # Preserve import-export lifecycle hooks for compatibility while
        # skipping model saves; all intended writes happen in `_apply_row()`.
        self.before_save_instance(instance, row, **kwargs)
        self.after_save_instance(instance, row, **kwargs)

    @override
    def after_import(self, dataset: Dataset, result: Any, **kwargs: Any) -> None:
        super().after_import(dataset, result, **kwargs)

        try:
            totals = dict(getattr(result, "totals", {}) or {})
        except Exception:
            totals = {}

        if totals:
            logger.info(
                "Organization membership CSV import result totals: %s",
                " ".join(f"{key}={totals[key]}" for key in sorted(totals)),
            )

        row_errors_obj = getattr(result, "row_errors", None)
        row_errors_pairs: list[tuple[int, list[Any]]] = []
        row_errors_flat: list[Any] = []

        if callable(row_errors_obj):
            try:
                raw = list(row_errors_obj())
            except TypeError:
                raw = []

            if raw and isinstance(raw[0], tuple) and len(raw[0]) == 2:
                row_errors_pairs = [(int(row_number), list(errors or [])) for row_number, errors in raw]
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
                        org_id = self._row_value(err_row, "organization_id")
                        org_name = self._row_value(err_row, "organization_name")
                    except Exception:
                        org_id = ""
                        org_name = ""

                    logger.error(
                        "Organization membership CSV import: row exception row=%s organization_id=%r organization_name=%r exc=%r\n%s",
                        row_number,
                        org_id,
                        org_name,
                        getattr(err, "error", None),
                        getattr(err, "traceback", ""),
                    )

                if shown > limit:
                    break

            total = sum(len(errors) for _row_number, errors in row_errors_pairs)
            if total > limit:
                logger.error(
                    "Organization membership CSV import: %d more row exceptions not shown",
                    total - limit,
                )
        elif row_errors_flat:
            limit = 25
            for err in row_errors_flat[:limit]:
                err_row = getattr(err, "row", None)
                row_number = getattr(err, "number", None)
                try:
                    org_id = self._row_value(err_row, "organization_id")
                    org_name = self._row_value(err_row, "organization_name")
                except Exception:
                    org_id = ""
                    org_name = ""

                logger.error(
                    "Organization membership CSV import: row exception row=%s organization_id=%r organization_name=%r exc=%r\n%s",
                    row_number,
                    org_id,
                    org_name,
                    getattr(err, "error", None),
                    getattr(err, "traceback", ""),
                )

            if len(row_errors_flat) > limit:
                logger.error(
                    "Organization membership CSV import: %d more row exceptions not shown",
                    len(row_errors_flat) - limit,
                )

        decision_summary = " ".join(
            f"{decision}={self._decision_counts[decision]}" for decision in sorted(self._decision_counts)
        )
        if decision_summary:
            logger.info(
                "Organization membership CSV import summary: %s unmatched=%d dry_run=%s",
                decision_summary,
                len(self._unmatched),
                bool(kwargs.get("dry_run")),
            )

        if self._skip_reason_counts:
            top = sorted(self._skip_reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            logger.info(
                "Organization membership CSV import skip reasons (top %d): %s",
                len(top),
                "; ".join(f"{reason} ({count})" for reason, count in top),
            )

        if not self._unmatched:
            return

        unmatched_dataset = Dataset()
        unmatched_dataset.headers = ["row_number", "organization_id", "organization_name", "skip_reason"]
        for item in self._unmatched:
            unmatched_dataset.append(
                [
                    item.get("row_number", ""),
                    sanitize_csv_cell(item.get("organization_id", "")),
                    sanitize_csv_cell(item.get("organization_name", "")),
                    item.get("skip_reason", ""),
                ]
            )

        attach_unmatched_csv_to_result(
            result,
            unmatched_dataset,
            "organization-membership-import-unmatched",
            "admin:core_organizationmembershipcsvimportlink_download_unmatched",
        )
