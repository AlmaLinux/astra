import hashlib
from collections.abc import Iterable
from typing import Any, override

from django import forms
from django.core.exceptions import ValidationError
from import_export import fields, resources
from import_export.forms import ConfirmImportForm, ImportForm
from tablib import Dataset

from core.address_geocoding import decompose_full_address_with_photon
from core.country_codes import is_valid_country_alpha2, normalize_country_alpha2
from core.csv_import_utils import (
    extract_csv_headers_from_uploaded_file,
    norm_csv_header,
    normalize_csv_email,
    set_form_column_field_choices,
)
from core.freeipa.user import FreeIPAUser
from core.models import Organization
from core.views_utils import _normalize_str

_ORG_COLUMN_FIELDS = (
    "name_column",
    "business_contact_name_column",
    "business_contact_email_column",
    "business_contact_phone_column",
    "pr_marketing_contact_name_column",
    "pr_marketing_contact_email_column",
    "pr_marketing_contact_phone_column",
    "technical_contact_name_column",
    "technical_contact_email_column",
    "technical_contact_phone_column",
    "website_column",
    "website_logo_column",
    "country_code_column",
    "full_address_column",
    "street_column",
    "city_column",
    "state_column",
    "postal_code_column",
    "representative_username_column",
    "representative_email_column",
)

_REQUIRED_ROW_FIELDS = (
    "name",
    "business_contact_name",
    "business_contact_email",
    "website",
    "country_code",
)

_ALL_ROW_FIELDS = tuple(field_name.removesuffix("_column") for field_name in _ORG_COLUMN_FIELDS)

_COLUMN_FALLBACK_NORMS: dict[str, tuple[str, ...]] = {
    "name": ("name", "organizationname"),
    "business_contact_name": ("businesscontactname",),
    "business_contact_email": ("businesscontactemail",),
    "business_contact_phone": ("businesscontactphone",),
    "pr_marketing_contact_name": ("prmarketingcontactname",),
    "pr_marketing_contact_email": ("prmarketingcontactemail",),
    "pr_marketing_contact_phone": ("prmarketingcontactphone",),
    "technical_contact_name": ("technicalcontactname",),
    "technical_contact_email": ("technicalcontactemail",),
    "technical_contact_phone": ("technicalcontactphone",),
    "website": ("website", "url"),
    "website_logo": ("websitelogo", "logo", "logourl"),
    "country_code": ("countrycode", "country"),
    "full_address": ("fulladdress", "address"),
    "street": ("street",),
    "city": ("city",),
    "state": ("state", "province"),
    "postal_code": ("postalcode", "zip", "zipcode"),
    "representative_username": ("representativeusername", "representative"),
    "representative_email": ("representativeemail",),
}


def required_organization_csv_columns() -> tuple[str, ...]:
    return _REQUIRED_ROW_FIELDS


def optional_organization_csv_columns() -> tuple[str, ...]:
    return tuple(field_name for field_name in _ALL_ROW_FIELDS if field_name not in _REQUIRED_ROW_FIELDS)

_EMAIL_FIELD = forms.EmailField(required=True)
_URL_FIELD = forms.URLField(required=True)


def _is_phone_value_valid(value: str) -> bool:
    raw = _normalize_str(value)
    if not raw:
        return False
    if len(raw) > 64:
        return False
    allowed = set("0123456789+()-. xX")
    return all(ch in allowed for ch in raw)


class OrganizationCSVImportForm(ImportForm):
    name_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    business_contact_name_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    business_contact_email_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    business_contact_phone_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    pr_marketing_contact_name_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    pr_marketing_contact_email_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    pr_marketing_contact_phone_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    technical_contact_name_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    technical_contact_email_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    technical_contact_phone_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    website_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    website_logo_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    country_code_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    full_address_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])

    street_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    city_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    state_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    postal_code_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])

    representative_username_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])
    representative_email_column = forms.ChoiceField(required=False, choices=[("", "Auto-detect")])

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        format_field = self.fields.get("format")
        if format_field is not None:
            format_choices = list(format_field.choices)
            if len(format_choices) == 1:
                format_field.initial = format_choices[0][0]
                format_field.widget = forms.HiddenInput()

        self.fields["representative_username_column"].help_text = (
            "Organizations are unclaimed by default. Provide representative_username or representative_email "
            "to suggest matches; you can select a representative during preview."
        )

        uploaded = self.files.get("import_file")
        if uploaded is None:
            return

        headers = extract_csv_headers_from_uploaded_file(uploaded)
        if not headers:
            return

        set_form_column_field_choices(form=self, field_names=_ORG_COLUMN_FIELDS, headers=headers)


class OrganizationCSVConfirmImportForm(ConfirmImportForm):
    name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    business_contact_name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    business_contact_email_column = forms.CharField(required=False, widget=forms.HiddenInput)
    business_contact_phone_column = forms.CharField(required=False, widget=forms.HiddenInput)
    pr_marketing_contact_name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    pr_marketing_contact_email_column = forms.CharField(required=False, widget=forms.HiddenInput)
    pr_marketing_contact_phone_column = forms.CharField(required=False, widget=forms.HiddenInput)
    technical_contact_name_column = forms.CharField(required=False, widget=forms.HiddenInput)
    technical_contact_email_column = forms.CharField(required=False, widget=forms.HiddenInput)
    technical_contact_phone_column = forms.CharField(required=False, widget=forms.HiddenInput)
    website_column = forms.CharField(required=False, widget=forms.HiddenInput)
    website_logo_column = forms.CharField(required=False, widget=forms.HiddenInput)
    country_code_column = forms.CharField(required=False, widget=forms.HiddenInput)
    full_address_column = forms.CharField(required=False, widget=forms.HiddenInput)

    street_column = forms.CharField(required=False, widget=forms.HiddenInput)
    city_column = forms.CharField(required=False, widget=forms.HiddenInput)
    state_column = forms.CharField(required=False, widget=forms.HiddenInput)
    postal_code_column = forms.CharField(required=False, widget=forms.HiddenInput)

    representative_username_column = forms.CharField(required=False, widget=forms.HiddenInput)
    representative_email_column = forms.CharField(required=False, widget=forms.HiddenInput)


class OrganizationCSVImportResource(resources.ModelResource):
    name = fields.Field(attribute="csv_name", column_name="Name", readonly=True)
    country_code = fields.Field(attribute="csv_country_code", column_name="Country", readonly=True)
    business_contact_email = fields.Field(
        attribute="csv_business_contact_email",
        column_name="Business Contact Email",
        readonly=True,
    )
    technical_contact_email = fields.Field(
        attribute="csv_technical_contact_email",
        column_name="Technical Contact Email",
        readonly=True,
    )
    representative_hint = fields.Field(
        attribute="csv_representative_hint",
        column_name="Representative Hint",
        readonly=True,
    )
    representative = fields.Field(
        attribute="selected_representative",
        column_name="Representative",
        readonly=True,
    )
    decision = fields.Field(attribute="decision", column_name="Decision", readonly=True)
    decision_reason = fields.Field(attribute="decision_reason", column_name="Decision Reason", readonly=True)

    class Meta:
        model = Organization
        import_id_fields = ()
        fields = (
            "name",
            "country_code",
            "business_contact_email",
            "technical_contact_email",
            "representative_hint",
            "representative",
            "decision",
            "decision_reason",
        )
        use_transactions = True
        use_bulk = False

    def __init__(
        self,
        *,
        actor_username: str = "",
        representative_selections: dict[str, str] | None = None,
        **column_overrides: str,
    ) -> None:
        super().__init__()
        self._actor_username = actor_username
        self._column_overrides = column_overrides
        self._representative_selections = representative_selections or {}

        self._headers: list[str] = []
        self._resolved_headers: dict[str, str | None] = {}

        self._username_cache: dict[str, FreeIPAUser] = {}
        self._username_lookup_cache: dict[str, FreeIPAUser | None] = {}
        self._email_to_usernames: dict[str, set[str]] = {}
        self._email_lookup_cache: dict[str, list[str]] = {}
        self._full_address_parts_cache: dict[str, dict[str, str]] = {}
        self._address_parts_cache: dict[str, dict[str, str]] = {}
        self._row_country_cache: dict[str, str] = {}
        self._row_unique_key_cache: dict[str, tuple[str, str]] = {}
        self._row_selection_key_cache: dict[str, str] = {}
        self._suggested_usernames_cache: dict[str, list[str]] = {}
        self._selected_representative_cache: dict[str, str] = {}
        self._decision_cache: dict[str, tuple[str, str]] = {}

        self._duplicate_csv_keys: set[tuple[str, str]] = set()
        self._existing_org_keys: set[tuple[str, str]] = set()
        self._existing_representatives: set[str] = set()

    @override
    def before_import(self, dataset: Dataset, **kwargs: Any) -> None:
        headers = list(dataset.headers or [])
        if not headers:
            raise ValueError("CSV has no headers")
        self._headers = headers
        header_by_norm = {norm_csv_header(header): header for header in headers if header}

        def resolve_header(field_name: str, *fallback_norms: str) -> str | None:
            raw = _normalize_str(self._column_overrides.get(field_name, ""))
            if raw:
                if raw in headers:
                    return raw
                normalized = norm_csv_header(raw)
                if normalized in header_by_norm:
                    return header_by_norm[normalized]
                raise ValueError(f"Column '{raw}' not found in CSV headers")

            for candidate in fallback_norms:
                resolved = header_by_norm.get(candidate)
                if resolved:
                    return resolved
            return None

        self._resolved_headers = {
            logical_name: resolve_header(f"{logical_name}_column", *fallbacks)
            for logical_name, fallbacks in _COLUMN_FALLBACK_NORMS.items()
        }

        users = FreeIPAUser.all()
        for user in users:
            username = _normalize_str(user.username).lower()
            if not username:
                continue
            self._username_cache[username] = user
            self._username_lookup_cache[username] = user
            email = normalize_csv_email(user.email)
            if email:
                self._email_to_usernames.setdefault(email, set()).add(username)

        self._email_lookup_cache = {}

        counts: dict[tuple[str, str], int] = {}
        for row in dataset.dict:
            key = self._row_unique_key(row)
            counts[key] = counts.get(key, 0) + 1
        self._duplicate_csv_keys = {key for key, count in counts.items() if count > 1}

        self._existing_org_keys = {
            (str(name or "").strip().casefold(), normalize_country_alpha2(country_code or ""))
            for name, country_code in Organization.objects.values_list("name", "country_code")
        }
        self._existing_representatives = {
            str(username or "").strip().lower()
            for username in Organization.objects.exclude(representative="").values_list("representative", flat=True)
            if str(username or "").strip()
        }

    def _row_cache_key(self, row: Any) -> str:
        parts = [self._row_value(row, field_name) for field_name in _ALL_ROW_FIELDS]
        payload = "\x1f".join(parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _row_value(self, row: Any, logical_name: str) -> str:
        header = self._resolved_headers.get(logical_name)
        if not header:
            return ""
        try:
            return _normalize_str(row.get(header, ""))
        except AttributeError:
            return ""

    def _row_country(self, row: Any) -> str:
        cache_key = self._row_cache_key(row)
        cached = self._row_country_cache.get(cache_key)
        if cached is not None:
            return cached

        country = normalize_country_alpha2(self._address_parts_for_row(row).get("country_code", ""))
        self._row_country_cache[cache_key] = country
        return country

    def _full_address_parts_for_row(self, row: Any) -> dict[str, str]:
        full_address = self._row_value(row, "full_address")
        if not full_address:
            return {}

        cached = self._full_address_parts_cache.get(full_address)
        if cached is not None:
            return cached

        parts = decompose_full_address_with_photon(full_address)
        self._full_address_parts_cache[full_address] = parts
        return parts

    def _address_parts_for_row(self, row: Any) -> dict[str, str]:
        cache_key = self._row_cache_key(row)
        cached = self._address_parts_cache.get(cache_key)
        if cached is not None:
            return cached

        split_country = normalize_country_alpha2(self._row_value(row, "country_code"))
        split_parts = {
            "street": self._row_value(row, "street"),
            "city": self._row_value(row, "city"),
            "state": self._row_value(row, "state"),
            "postal_code": self._row_value(row, "postal_code"),
            "country_code": split_country,
        }

        geocoded_parts = self._full_address_parts_for_row(row)
        geocoded_country = normalize_country_alpha2(geocoded_parts.get("country_code", ""))

        parts = {
            "street": split_parts["street"] or geocoded_parts.get("street", ""),
            "city": split_parts["city"] or geocoded_parts.get("city", ""),
            "state": split_parts["state"] or geocoded_parts.get("state", ""),
            "postal_code": split_parts["postal_code"] or geocoded_parts.get("postal_code", ""),
            "country_code": split_parts["country_code"] or geocoded_country,
        }
        self._address_parts_cache[cache_key] = parts
        return parts

    def _row_unique_key(self, row: Any) -> tuple[str, str]:
        cache_key = self._row_cache_key(row)
        cached = self._row_unique_key_cache.get(cache_key)
        if cached is not None:
            return cached

        name = self._row_value(row, "name").casefold()
        country_code = self._row_country(row)
        key = (name, country_code)
        self._row_unique_key_cache[cache_key] = key
        return key

    def _row_key_for_selection(self, row: Any) -> str:
        cache_key = self._row_cache_key(row)
        cached = self._row_selection_key_cache.get(cache_key)
        if cached is not None:
            return cached

        parts = [
            self._row_value(row, "name"),
            self._row_country(row),
            self._row_value(row, "business_contact_email"),
            self._row_value(row, "technical_contact_email"),
        ]
        payload = "|".join(parts)
        key = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        self._row_selection_key_cache[cache_key] = key
        return key

    def _is_duplicate_in_database(self, row: Any) -> bool:
        return self._row_unique_key(row) in self._existing_org_keys

    def _usernames_for_email_hint(self, email: str) -> list[str]:
        normalized = normalize_csv_email(email)
        if not normalized:
            return []

        cached = self._email_lookup_cache.get(normalized)
        if cached is not None:
            return cached

        usernames = sorted(self._email_to_usernames.get(normalized, set()))
        if not usernames:
            # Tier 2 fallback: query all usernames for this exact email.
            usernames = sorted({u.lower() for u in FreeIPAUser.find_usernames_by_email(normalized) if _normalize_str(u)})

        if not usernames:
            # Tier 3 fallback: find the first matching user object.
            user = FreeIPAUser.find_by_email(normalized)
            if user is not None and _normalize_str(user.username):
                usernames = [_normalize_str(user.username).lower()]

        self._email_lookup_cache[normalized] = usernames
        return usernames

    def _suggested_usernames_for_row(self, row: Any) -> list[str]:
        cache_key = self._row_cache_key(row)
        cached = self._suggested_usernames_cache.get(cache_key)
        if cached is not None:
            return cached

        username_hint = _normalize_str(self._row_value(row, "representative_username")).lower()
        if username_hint:
            # Username hints have explicit precedence over email hints.
            if username_hint in self._username_cache:
                suggestions = [username_hint]
                self._suggested_usernames_cache[cache_key] = suggestions
                return suggestions

            user = FreeIPAUser.get(username_hint)
            if user is not None and _normalize_str(user.username):
                normalized_username = _normalize_str(user.username).lower()
                self._username_lookup_cache[username_hint] = user
                suggestions = [normalized_username]
                self._suggested_usernames_cache[cache_key] = suggestions
                return suggestions

            self._suggested_usernames_cache[cache_key] = []
            return []

        email_hint = self._row_value(row, "representative_email")
        if email_hint:
            suggestions = self._usernames_for_email_hint(email_hint)
            self._suggested_usernames_cache[cache_key] = suggestions
            return suggestions

        # No explicit representative hints provided; fall back to organization
        # contact emails to propose possible representatives.
        candidates: set[str] = set()
        for field_name in (
            "business_contact_email",
            "technical_contact_email",
            "pr_marketing_contact_email",
        ):
            value = self._row_value(row, field_name)
            candidates.update(self._usernames_for_email_hint(value))

        suggestions = sorted(candidates)
        self._suggested_usernames_cache[cache_key] = suggestions
        return suggestions

    def _selected_representative_for_row(self, row: Any) -> str:
        cache_key = self._row_cache_key(row)
        cached = self._selected_representative_cache.get(cache_key)
        if cached is not None:
            return cached

        row_key = self._row_key_for_selection(row)
        explicit = _normalize_str(self._representative_selections.get(row_key, "")).lower()
        if explicit:
            self._selected_representative_cache[cache_key] = explicit
            return explicit

        suggestions = self._suggested_usernames_for_row(row)
        if suggestions:
            selected = suggestions[0]
            self._selected_representative_cache[cache_key] = selected
            return selected

        self._selected_representative_cache[cache_key] = ""
        return ""

    def _validate_selected_representative(self, row: Any) -> tuple[bool, str]:
        selected = self._selected_representative_for_row(row)
        if not selected:
            return (True, "")

        if selected in self._username_lookup_cache:
            candidate = self._username_lookup_cache[selected]
        else:
            candidate = FreeIPAUser.get(selected)
            self._username_lookup_cache[selected] = candidate

        if candidate is None:
            return (False, "Representative not found (organization will be unclaimed)")

        if selected in self._existing_representatives:
            return (False, "Representative already represents another organization")

        return (True, "")

    def _validate_required_fields(self, row: Any) -> tuple[bool, str]:
        for field_name in _REQUIRED_ROW_FIELDS:
            value = self._row_country(row) if field_name == "country_code" else self._row_value(row, field_name)
            if not value:
                return (False, f"Missing required field: {field_name}")
        return (True, "")

    def _validate_contact_fields(self, row: Any) -> tuple[bool, str]:
        for field_name in ("business_contact_email", "pr_marketing_contact_email", "technical_contact_email"):
            value = self._row_value(row, field_name)
            if not value:
                continue
            try:
                _EMAIL_FIELD.clean(value)
            except ValidationError:
                return (False, f"Invalid email address: {field_name}")

        for field_name in ("website", "website_logo"):
            value = self._row_value(row, field_name)
            if not value:
                continue
            try:
                _URL_FIELD.clean(value)
            except ValidationError:
                return (False, f"Invalid URL: {field_name}")

        for field_name in ("business_contact_phone", "pr_marketing_contact_phone", "technical_contact_phone"):
            value = self._row_value(row, field_name)
            if not value:
                continue
            if not _is_phone_value_valid(value):
                return (False, f"Invalid phone number: {field_name}")

        country_code = self._row_country(row)
        if not is_valid_country_alpha2(country_code):
            return (False, "Invalid country code")

        return (True, "")

    def _decision_for_row(self, row: Any) -> tuple[str, str]:
        cache_key = self._row_cache_key(row)
        cached = self._decision_cache.get(cache_key)
        if cached is not None:
            return cached

        ok_required, required_reason = self._validate_required_fields(row)
        if not ok_required:
            decision = ("SKIP", required_reason)
            self._decision_cache[cache_key] = decision
            return decision

        ok_contacts, contacts_reason = self._validate_contact_fields(row)
        if not ok_contacts:
            decision = ("SKIP", contacts_reason)
            self._decision_cache[cache_key] = decision
            return decision

        row_key = self._row_unique_key(row)
        if row_key in self._duplicate_csv_keys:
            decision = ("SKIP", "Duplicate organization in CSV")
            self._decision_cache[cache_key] = decision
            return decision

        if self._is_duplicate_in_database(row):
            decision = ("SKIP", "Organization already exists")
            self._decision_cache[cache_key] = decision
            return decision

        valid_rep, rep_reason = self._validate_selected_representative(row)
        if not valid_rep:
            decision = ("SKIP", rep_reason)
            self._decision_cache[cache_key] = decision
            return decision

        decision = ("IMPORT", "Ready to import")
        self._decision_cache[cache_key] = decision
        return decision

    def _organization_kwargs_for_row(self, row: Any) -> dict[str, str]:
        address_parts = self._address_parts_for_row(row)
        return {
            "name": self._row_value(row, "name"),
            "business_contact_name": self._row_value(row, "business_contact_name"),
            "business_contact_email": self._row_value(row, "business_contact_email"),
            "business_contact_phone": self._row_value(row, "business_contact_phone"),
            "pr_marketing_contact_name": self._row_value(row, "pr_marketing_contact_name"),
            "pr_marketing_contact_email": self._row_value(row, "pr_marketing_contact_email"),
            "pr_marketing_contact_phone": self._row_value(row, "pr_marketing_contact_phone"),
            "technical_contact_name": self._row_value(row, "technical_contact_name"),
            "technical_contact_email": self._row_value(row, "technical_contact_email"),
            "technical_contact_phone": self._row_value(row, "technical_contact_phone"),
            "website": self._row_value(row, "website"),
            "website_logo": self._row_value(row, "website_logo"),
            "country_code": address_parts["country_code"],
            "street": address_parts["street"],
            "city": address_parts["city"],
            "state": address_parts["state"],
            "postal_code": address_parts["postal_code"],
            "representative": self._selected_representative_for_row(row),
        }

    def _populate_preview_fields(self, instance: Organization, row: Any) -> None:
        representative_hints = [
            _normalize_str(self._row_value(row, "representative_username")),
            _normalize_str(self._row_value(row, "representative_email")),
        ]
        representative_hints = [hint for hint in representative_hints if hint]
        suggestions = self._suggested_usernames_for_row(row)

        instance.csv_name = self._row_value(row, "name")
        instance.csv_country_code = self._row_country(row)
        instance.csv_business_contact_email = self._row_value(row, "business_contact_email")
        instance.csv_technical_contact_email = self._row_value(row, "technical_contact_email")
        instance.csv_representative_hint = ", ".join(representative_hints)
        instance.representative_options = suggestions
        instance.selected_representative = self._selected_representative_for_row(row)
        instance.row_key = self._row_key_for_selection(row)

        decision, reason = self._decision_for_row(row)
        instance.decision = decision
        instance.decision_reason = reason

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
    def before_import_row(self, row: Any, **kwargs: Any) -> None:
        # Warm row-level caches once. This mirrors the membership importer
        # strategy of doing heavier matching work ahead of repeated callbacks.
        self._decision_for_row(row)
        self._row_key_for_selection(row)
        self._suggested_usernames_for_row(row)
        self._selected_representative_for_row(row)

    @override
    def skip_row(self, instance: Any, original: Any, row: Any, import_validation_errors: Any = None) -> bool:
        self._populate_preview_fields(instance, row)
        decision, _reason = self._decision_for_row(row)
        return decision != "IMPORT"

    @override
    def import_instance(self, instance: Organization, row: Any, **kwargs: Any) -> None:
        super().import_instance(instance, row, **kwargs)
        self._populate_preview_fields(instance, row)

        decision, _reason = self._decision_for_row(row)
        if decision != "IMPORT":
            return

        kwargs_by_field = self._organization_kwargs_for_row(row)
        for field_name, value in kwargs_by_field.items():
            setattr(instance, field_name, value)

    @override
    def save_instance(self, instance: Any, is_create: bool, row: Any, **kwargs: Any) -> None:
        if bool(kwargs.get("dry_run")):
            return

        decision, _reason = self._decision_for_row(row)
        if decision != "IMPORT":
            return

        # Keep persistence resilient even if import-export changes callback
        # ordering; this importer is create-only and field assignment is
        # entirely row-driven.
        kwargs_by_field = self._organization_kwargs_for_row(row)
        for field_name, value in kwargs_by_field.items():
            setattr(instance, field_name, value)

        super().save_instance(instance, is_create, row, **kwargs)

        selected = _normalize_str(kwargs_by_field.get("representative", "")).lower()
        if selected:
            self._existing_representatives.add(selected)


def iter_representative_selection_items(post_items: Iterable[tuple[str, str]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, value in post_items:
        if not key.startswith("representative_for_"):
            continue
        row_key = key.removeprefix("representative_for_").strip()
        username = _normalize_str(value).lower()
        if row_key and username:
            mapping[row_key] = username
    return mapping
