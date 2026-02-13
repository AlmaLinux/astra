from dataclasses import dataclass

import pycountry
from django.conf import settings


@dataclass(frozen=True, slots=True)
class CountryCodeStatus:
    code: str | None
    is_valid: bool


@dataclass(frozen=True, slots=True)
class EmbargoedCountryMatch:
    code: str
    label: str


def country_attr_name() -> str:
    name = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR or "").strip()
    return name or "c"


def _first_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return _first_value(value[0]) if value else ""
    return str(value).strip()


def normalize_country_alpha2(value: object) -> str:
    return _first_value(value).upper()


def is_valid_country_alpha2(code: str) -> bool:
    c = (code or "").strip().upper()
    if len(c) != 2 or not c.isalpha():
        return False
    return pycountry.countries.get(alpha_2=c) is not None


def country_code_status_from_user_data(user_data: dict | None) -> CountryCodeStatus:
    if not isinstance(user_data, dict):
        return CountryCodeStatus(code=None, is_valid=False)

    attr = country_attr_name()
    raw = user_data.get(attr)
    if raw is None:
        raw = user_data.get(attr.lower())
    if raw is None:
        raw = user_data.get(attr.upper())
    code = normalize_country_alpha2(raw)
    if not code:
        return CountryCodeStatus(code=None, is_valid=False)

    return CountryCodeStatus(code=code, is_valid=is_valid_country_alpha2(code))


def embargoed_country_codes_from_settings() -> set[str]:
    codes: set[str] = set()
    for raw in settings.MEMBERSHIP_EMBARGOED_COUNTRY_CODES or []:
        code = str(raw or "").strip().upper()
        if code and is_valid_country_alpha2(code):
            codes.add(code)
    return codes


def country_name_from_code(code: str) -> str:
    normalized = str(code or "").strip().upper()
    record = pycountry.countries.get(alpha_2=normalized)
    if record is None:
        return normalized

    # Prefer common names when available (e.g. "Iran" vs "Iran, Islamic Republic of").
    common_name = getattr(record, "common_name", None)
    if common_name:
        return str(common_name)

    return str(record.name or normalized)


def country_label_from_code(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if not normalized:
        return ""
    return f"{country_name_from_code(normalized)} ({normalized})"


def embargoed_country_match_from_user_data(
    *,
    user_data: dict | None,
    embargoed_codes: set[str] | None = None,
) -> EmbargoedCountryMatch | None:
    status = country_code_status_from_user_data(user_data)
    if not status.is_valid or not status.code:
        return None

    normalized_code = status.code.strip().upper()
    active_embargoed_codes = embargoed_codes if embargoed_codes is not None else embargoed_country_codes_from_settings()
    if normalized_code not in active_embargoed_codes:
        return None

    return EmbargoedCountryMatch(
        code=normalized_code,
        label=country_label_from_code(normalized_code),
    )


def embargoed_country_label_from_user_data(
    *,
    user_data: dict | None,
    embargoed_codes: set[str] | None = None,
) -> str | None:
    match = embargoed_country_match_from_user_data(
        user_data=user_data,
        embargoed_codes=embargoed_codes,
    )
    return match.label if match is not None else None
