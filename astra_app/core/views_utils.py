from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from core.agreements import has_enabled_agreements
from core.backends import FreeIPAUser, _invalidate_user_cache, _invalidate_users_list_cache
from core.country_codes import country_code_status_from_user_data

logger = logging.getLogger(__name__)


def settings_context(active_tab: str) -> dict[str, object]:
    return {
        "active_tab": active_tab,
        "show_agreements_tab": has_enabled_agreements(),
    }


def block_action_without_country_code(
    request: HttpRequest,
    *,
    user_data: dict | None,
    action_label: str,
) -> HttpResponse | None:
    """Enforce that a user has a valid country code before performing an action.

    This is a legal/compliance requirement used across multiple flows (e.g.
    settings updates, membership requests).
    """

    status = country_code_status_from_user_data(user_data)
    if status.is_valid:
        return None

    messages.error(
        request,
        f"A valid country code is required before you can {action_label}. Please set it on the Profile tab.",
    )
    return redirect(f"{reverse('settings')}#profile")


_ATTR_NOT_ALLOWED_RE = re.compile(r"attribute\s+['\"]?([a-zA-Z0-9_-]+)['\"]?\s+not\s+allowed", re.IGNORECASE)


def _parse_not_allowed_attr(exc: Exception) -> str | None:
    message = str(exc) or ""
    m = _ATTR_NOT_ALLOWED_RE.search(message)
    if not m:
        return None
    return m.group(1)


def _data_get(data: dict[str, Any], attr: str, default: Any = None) -> Any:
    # FreeIPA/JSON results typically use lower-case keys, but LDAP attr names are case-insensitive.
    if attr in data:
        return data.get(attr, default)
    return data.get(attr.lower(), default)


def _first(data: dict[str, Any], key: str, default: Any = None) -> Any:
    value = _data_get(data, key, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def _bool_to_ipa(value: bool) -> str:
    # FreeIPA generally accepts TRUE/FALSE for LDAP boolean-ish attrs.
    return "TRUE" if value else "FALSE"


def _bool_from_ipa(value: object, default: bool = False) -> bool:
    """Parse FreeIPA boolean-ish attribute values.

    FreeIPA may return boolean LDAP-ish values as strings ("TRUE"/"FALSE") or
    actual Python bools depending on the client and schema.
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return _bool_from_ipa(value[0], default=default) if value else default

    s = str(value).strip().upper()
    if s in {"TRUE", "T", "YES", "Y", "1", "ON"}:
        return True
    if s in {"FALSE", "F", "NO", "N", "0", "OFF", ""}:
        return False
    return default


def _update_user_attrs(
    username: str,
    *,
    direct_updates: dict[str, object] | None = None,
    addattrs: list[str] | None = None,
    setattrs: list[str] | None = None,
    delattrs: list[str] | None = None,
) -> tuple[list[str], bool]:
    """Persist changes to FreeIPA.

    - Built-in user_mod options go in `direct_updates` (keys like o_givenname)
    - Generic attribute updates go via `o_addattr` / `o_setattr` / `o_delattr`

    Returns: (skipped_attrs, applied)
    - skipped_attrs: list of attribute names FreeIPA rejected as not allowed
    - applied: False if nothing was applied (e.g., all requested attrs were disallowed)
    """

    direct_updates = dict(direct_updates or {})
    addattrs = list(addattrs or [])
    setattrs = list(setattrs or [])
    delattrs = list(delattrs or [])

    # Keep FreeIPA's name-related fields synchronized whenever profile updates
    # touch givenname/sn. Self-service settings use `user_mod` directly (not
    # FreeIPAUser.save), so this is the central enforcement point.
    if "o_givenname" in direct_updates or "o_sn" in direct_updates:
        existing = FreeIPAUser.get(username)
        current_first = existing.first_name if existing is not None else ""
        current_last = existing.last_name if existing is not None else ""
        new_first = str(direct_updates.get("o_givenname", current_first) or "")
        new_last = str(direct_updates.get("o_sn", current_last) or "")
        derived = f"{new_first or ''} {new_last or ''}"
        direct_updates["o_cn"] = derived
        direct_updates["o_gecos"] = derived
        direct_updates["o_displayname"] = derived

        initials = f"{(new_first.strip()[:1] or '').upper()}{(new_last.strip()[:1] or '').upper()}"
        if initials:
            direct_updates["o_initials"] = initials

    def _attr_names_from_setattrs(values: list[str]) -> list[str]:
        names: list[str] = []
        for item in values:
            if "=" in item:
                names.append(item.split("=", 1)[0])
            else:
                names.append(item)
        return sorted(set(names))

    def _attr_names_from_delattrs(values: list[str]) -> list[str]:
        names: list[str] = []
        for item in values:
            if "=" in item:
                names.append(item.split("=", 1)[0])
            elif item.endswith("="):
                names.append(item[:-1])
            else:
                names.append(item)
        return sorted(set(names))

    logger.debug(
        "FreeIPA user_mod: username=%s direct_keys=%s addattr_count=%d setattr_count=%d delattr_count=%d",
        username,
        sorted(direct_updates.keys()),
        len(addattrs),
        len(setattrs),
        len(delattrs),
    )

    client = FreeIPAUser.get_client()

    skipped_attrs: list[str] = []
    attempts = 0
    working_addattrs = list(addattrs)
    working_setattrs = list(setattrs)
    working_delattrs = list(delattrs)
    internal_clear_fallback_used = False

    def _delattr_name(item: str) -> str:
        # delattr can be specified as "attr=" (clear) or "attr=value" (remove one value)
        if "=" in item:
            return item.split("=", 1)[0]
        return item[:-1] if item.endswith("=") else item

    def _filter_delattrs(values: list[str], *, remove_attr: str) -> list[str]:
        return [v for v in values if _delattr_name(v) != remove_attr]

    def _is_internal_error(exc: Exception) -> bool:
        return "internal error" in (str(exc) or "").lower()

    while True:
        attempts += 1
        call_updates = dict(direct_updates)
        if working_addattrs:
            call_updates["o_addattr"] = working_addattrs
        if working_setattrs:
            call_updates["o_setattr"] = working_setattrs
        if working_delattrs:
            call_updates["o_delattr"] = working_delattrs

        try:
            client.user_mod(username, **call_updates)
            break
        except Exception as e:
            attr = _parse_not_allowed_attr(e)
            if attr:
                # Expected on some FreeIPA deployments: certain schema attrs are not editable.
                logger.info(
                    "FreeIPA user_mod rejected attribute: username=%s attr=%s direct_keys=%s",
                    username,
                    attr,
                    sorted(direct_updates.keys()),
                )
            else:
                logger.warning(
                    "FreeIPA user_mod failed: username=%s error=%s direct_keys=%s addattr_attrs=%s setattr_attrs=%s delattr_attrs=%s",
                    username,
                    e,
                    sorted(direct_updates.keys()),
                    _attr_names_from_setattrs(working_addattrs),
                    _attr_names_from_setattrs(working_setattrs),
                    _attr_names_from_delattrs(working_delattrs),
                )

            if attr:
                if attempts >= 5:
                    raise

                new_addattrs = [s for s in working_addattrs if not s.startswith(f"{attr}=")]
                new_setattrs = [s for s in working_setattrs if not s.startswith(f"{attr}=")]
                new_delattrs = _filter_delattrs(working_delattrs, remove_attr=attr)
                if new_addattrs == working_addattrs and new_setattrs == working_setattrs and new_delattrs == working_delattrs:
                    raise

                skipped_attrs.append(attr)
                working_addattrs = new_addattrs
                working_setattrs = new_setattrs
                working_delattrs = new_delattrs

                if not direct_updates and not working_addattrs and not working_setattrs and not working_delattrs:
                    # The only attempted changes were for attributes FreeIPA doesn't allow.
                    return skipped_attrs, False

                continue

            if (
                not internal_clear_fallback_used
                and _is_internal_error(e)
                and working_delattrs
                and not working_addattrs
                and not working_setattrs
                and all(d.endswith("=") for d in working_delattrs)
            ):
                internal_clear_fallback_used = True
                # Some FreeIPA deployments error on `o_delattr: ["attr="]` clears.
                logger.info(
                    "FreeIPA clear via delattr hit internal error; retrying via setattr: username=%s attrs=%s",
                    username,
                    _attr_names_from_delattrs(working_delattrs),
                )
                working_setattrs = list(working_delattrs)
                working_delattrs = []
                continue

            logger.exception("FreeIPA user_mod unexpected failure username=%s", username)
            raise

    # Invalidate caches so lists/details refresh immediately.
    try:
        _invalidate_user_cache(username)
        _invalidate_users_list_cache()
    except Exception:
        pass

    # Re-warm the user object so the next page load reflects the change.
    try:
        FreeIPAUser.get(username)
    except Exception:
        pass

    return skipped_attrs, True


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _value_to_text(value: object) -> str:
    if isinstance(value, list):
        # Prefer one-per-line for multi-valued attributes.
        return "\n".join([str(v) for v in value if _normalize_str(v)])
    return _normalize_str(value)


def _value_to_csv(value: object) -> str:
    if isinstance(value, list):
        return ", ".join([str(v).strip() for v in value if _normalize_str(v)])
    s = _normalize_str(value)
    if "\n" in s:
        parts = [p.strip() for p in s.replace("\r", "").split("\n") if p.strip()]
        return ", ".join(parts)
    return s


def _add_change(
    *,
    updates: dict[str, object],
    delattrs: list[str],
    attr: str,
    current_value: object,
    new_value: object,
    transform=None,
) -> None:
    """Add a change for a single-valued attribute.

    - If unchanged: do nothing
    - If new is empty and current is non-empty: clear via delattr
    - Else: set via explicit option
    """

    current_s = _normalize_str(current_value)
    new_s = _normalize_str(new_value)

    if transform is not None and new_s:
        new_s = transform(new_s)

    if current_s == new_s:
        return

    if new_s == "":
        if current_s != "":
            delattrs.append(f"{attr}=")
        return

    updates[f"o_{attr}"] = new_s


def _add_change_setattr(
    *,
    setattrs: list[str],
    delattrs: list[str],
    attr: str,
    current_value: object,
    new_value: object,
    transform=None,
) -> None:
    """Add a change for an attribute using FreeIPA's generic setattr/delattr."""

    current_s = _normalize_str(current_value)
    new_s = _normalize_str(new_value)

    if transform is not None and new_s:
        new_s = transform(new_s)

    if current_s == new_s:
        return

    if new_s == "":
        if current_s != "":
            delattrs.append(f"{attr}=")
        return

    setattrs.append(f"{attr}={new_s}")


def _add_change_list(
    *,
    updates: dict[str, object],
    delattrs: list[str],
    attr: str,
    current_values: object,
    new_values: list[str],
) -> None:
    """Add a change for a multi-valued attribute."""

    if isinstance(current_values, str):
        current_list = [current_values]
    elif isinstance(current_values, list):
        current_list = [str(v) for v in current_values]
    else:
        current_list = []

    current_norm = sorted([_normalize_str(v) for v in current_list if _normalize_str(v)])
    new_norm = sorted([_normalize_str(v) for v in (new_values or []) if _normalize_str(v)])

    if current_norm == new_norm:
        return

    if not new_norm:
        if current_norm:
            delattrs.append(f"{attr}=")
        return

    updates[f"o_{attr}"] = new_norm


def _add_change_list_setattr(
    *,
    addattrs: list[str],
    setattrs: list[str],
    delattrs: list[str],
    attr: str,
    current_values: object,
    new_values: list[str],
) -> None:
    """Update a multi-valued attribute using delattr+addattr.

    Avoids `attr=` clears by applying a diff:
    - remove values via `o_delattr: ["attr=value", ...]`
    - add values via `o_addattr: ["attr=value", ...]`
    """

    if isinstance(current_values, str):
        current_list = [current_values]
    elif isinstance(current_values, list):
        current_list = [str(v) for v in current_values]
    else:
        current_list = []

    current_norm = sorted({_normalize_str(v) for v in current_list if _normalize_str(v)})
    new_norm = sorted({_normalize_str(v) for v in (new_values or []) if _normalize_str(v)})

    if current_norm == new_norm:
        return

    if not new_norm:
        for v in current_norm:
            delattrs.append(f"{attr}={v}")
        return

    to_remove = [v for v in current_norm if v not in new_norm]
    to_add = [v for v in new_norm if v not in current_norm]

    for v in to_remove:
        delattrs.append(f"{attr}={v}")
    for v in to_add:
        addattrs.append(f"{attr}={v}")


def _split_lines(value: str) -> list[str]:
    lines = [line.strip() for line in (value or "").splitlines()]
    return [line for line in lines if line]


def _split_list_field(value: str) -> list[str]:
    # Allow comma-separated in addition to newlines.
    out: list[str] = []
    for line in _split_lines(value):
        for part in line.split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out


def _form_label_for_attr(form: object, attr: str) -> str | None:
    fields = getattr(form, "fields", {})
    if attr in fields:
        field = fields[attr]
        return getattr(field, "label", None) or attr

    lower = attr.lower()
    for k, field in fields.items():
        if str(k).lower() == lower:
            return getattr(field, "label", None) or str(k)
    return None


def _get_full_user(username: str) -> FreeIPAUser | None:
    return FreeIPAUser.get(username)


def _debug_message_for_exception(exc: Exception) -> str:
    if settings.DEBUG:
        return str(exc)
    return ""
