"""Low-level FreeIPA value parsing utilities.

This module has NO internal app imports, so it can be safely imported from any
module (backends, views_utils, etc.) without circular-import risk.
"""

from collections.abc import Callable


def bool_from_ipa(value: object, default: bool = False) -> bool:
    """Parse FreeIPA boolean-ish attribute values.

    FreeIPA may return boolean LDAP-ish values as strings ("TRUE"/"FALSE") or
    actual Python bools depending on the client and schema.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return bool_from_ipa(value[0], default=default) if value else default

    s = str(value).strip().upper()
    if s in {"TRUE", "T", "YES", "Y", "1", "ON"}:
        return True
    if s in {"FALSE", "F", "NO", "N", "0", "OFF", ""}:
        return False
    return default

def bool_to_ipa(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def sync_set_membership(
    desired: set[str],
    current: set[str],
    add_fn: Callable[[str], object],
    remove_fn: Callable[[str], object],
    *,
    pre_add_check: Callable[[str], None] | None = None,
) -> None:
    """Sync a FreeIPA set attribute (members, sponsors, groups, etc.).

    Computes the diff between *desired* and *current*, then calls *add_fn*
    for items to add (sorted) and *remove_fn* for items to remove (sorted).

    If *pre_add_check* is provided it is called before each add — it should
    raise to abort the add (e.g. missing agreement check).
    """
    for item in sorted(desired - current):
        if pre_add_check is not None:
            pre_add_check(item)
        add_fn(item)
    for item in sorted(current - desired):
        remove_fn(item)
