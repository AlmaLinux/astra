from __future__ import annotations

from dataclasses import dataclass

import pycountry
from django.conf import settings


@dataclass(frozen=True, slots=True)
class CountryCodeStatus:
    code: str | None
    is_valid: bool


def country_attr_name() -> str:
    name = str(getattr(settings, "SELF_SERVICE_ADDRESS_COUNTRY_ATTR", "c") or "").strip()
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
    record = pycountry.countries.get(alpha_2=str(code or "").strip().upper())
    if record is None:
        return code
    return str(record.name or code)
