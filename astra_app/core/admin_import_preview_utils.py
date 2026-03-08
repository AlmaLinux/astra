from collections.abc import Mapping, Sequence
from typing import Any

from django import forms
from django.core.paginator import Paginator

from core.csv_import_utils import get_result_rows


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


def build_result_preview_transport(
    *,
    result: Any,
    request_get: Mapping[str, Any],
    instance_decision_attr: str,
    current_selected_row_numbers: str = "",
    fallback_attr_name: str | None = None,
    per_page_default: int = 50,
    per_page_min: int = 50,
) -> dict[str, Any]:
    preview_rows = get_result_rows(result, "valid_rows", fallback_attr_name=fallback_attr_name)
    preview_context = build_import_preview_context(
        valid_rows=preview_rows,
        request_get=request_get,
        instance_decision_attr=instance_decision_attr,
        per_page_default=per_page_default,
        per_page_min=per_page_min,
    )

    match_row_numbers = preview_context["match_row_numbers"]
    all_match_row_numbers_csv = ",".join(str(n) for n in match_row_numbers)
    selected_from_query = str(request_get.get("selected_row_numbers", "") or "").strip()
    current_selected = str(current_selected_row_numbers or "").strip()

    return {
        **preview_context,
        "preview_rows": preview_rows,
        "all_match_row_numbers_csv": all_match_row_numbers_csv,
        "selected_row_numbers": selected_from_query or current_selected or all_match_row_numbers_csv,
    }


def apply_selected_row_numbers_initial(confirm_form: forms.Form, selected_row_numbers: str) -> None:
    if not selected_row_numbers:
        return

    confirm_form.initial["selected_row_numbers"] = selected_row_numbers
    if "selected_row_numbers" in confirm_form.fields:
        confirm_form.fields["selected_row_numbers"].initial = selected_row_numbers
