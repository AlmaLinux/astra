from collections.abc import Mapping, Sequence
from typing import Any

from django.core.paginator import Paginator


def build_import_preview_context(
    *,
    valid_rows: Sequence[Any],
    request_get: Mapping[str, Any],
    instance_decision_attr: str,
    per_page_default: int = 50,
    per_page_min: int = 50,
) -> dict[str, Any]:
    matches: list[Any] = []
    skipped: list[Any] = []
    match_row_numbers: list[int] = []

    for index, row_result in enumerate(valid_rows, start=1):
        instance = row_result.instance if hasattr(row_result, "instance") else None
        import_type = str(getattr(row_result, "import_type", "") or "")
        if import_type:
            is_match = import_type != "skip"
        elif instance is not None and hasattr(instance, instance_decision_attr):
            is_match = getattr(instance, instance_decision_attr) == "IMPORT"
        else:
            is_match = False

        row_number = getattr(row_result, "number", None)
        if row_number is None:
            row_number = getattr(getattr(row_result, "row", None), "number", None)
        if row_number is None:
            row_number = getattr(getattr(row_result, "original", None), "number", None)
        if row_number is None:
            row_number = index

        try:
            astra_row_number = int(row_number)
        except (TypeError, ValueError):
            astra_row_number = index
        row_result.astra_row_number = astra_row_number

        if is_match:
            matches.append(row_result)
            match_row_numbers.append(astra_row_number)
        else:
            skipped.append(row_result)

    try:
        per_page = int(str(request_get.get("per_page", str(per_page_default))))
    except ValueError:
        per_page = per_page_default
    per_page = max(per_page, per_page_min)

    matches_page_obj = Paginator(matches, per_page).get_page(request_get.get("matches_page") or "1")
    skipped_page_obj = Paginator(skipped, per_page).get_page(request_get.get("skipped_page") or "1")

    return {
        "matches_page_obj": matches_page_obj,
        "skipped_page_obj": skipped_page_obj,
        "match_row_numbers": sorted(set(match_row_numbers)),
        "preview_summary": {
            "total": len(valid_rows),
            "to_import": len(matches),
            "skipped": len(skipped),
        },
    }
