from core.ipa_utils import bool_from_ipa


def _clean_str_list(values: object) -> list[str]:
    """Normalize FreeIPA multi-valued attributes into a clean list[str]."""

    if values is None:
        return []
    if isinstance(values, str):
        normalized = values.strip()
        return [normalized] if normalized else []
    if isinstance(values, (list, tuple, set)):
        output: list[str] = []
        seen: set[str] = set()
        for item in values:
            if item is None:
                continue
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            output.append(normalized)
            seen.add(normalized)
        return output

    normalized = str(values).strip()
    return [normalized] if normalized else []


def _first_attr_ci(data: dict[str, object], key: str, default: object | None = None) -> object | None:
    """Return the first value for an attribute key, case-insensitively."""

    if key in data:
        value = data.get(key, default)
    else:
        key_lower = key.lower()
        value = data.get(key_lower)
        if value is None:
            for data_key, data_value in data.items():
                if str(data_key).lower() == key_lower:
                    value = data_value
                    break
            else:
                value = default

    if isinstance(value, list):
        return value[0] if value else default
    return value


__all__ = ["_clean_str_list", "_first_attr_ci", "bool_from_ipa"]
