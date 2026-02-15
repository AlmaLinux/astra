from typing import Any

from django.core.paginator import Paginator
from django.http import HttpRequest
from django.template import Context
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.views_utils import _normalize_str, build_page_url_prefix, pagination_window


def parse_grid_query(request: HttpRequest | None) -> tuple[str, str | None, str, str]:
    if request is None:
        return "", None, "", "?page="

    query = _normalize_str(request.GET.get("q"))
    page_number = _normalize_str(request.GET.get("page")) or None

    base_query, page_url_prefix = build_page_url_prefix(request.GET, page_param="page")
    return query, page_number, base_query, page_url_prefix


def resolve_grid_request(context: Context) -> tuple[HttpRequest | None, str, str | None, str, str]:
    request = context.get("request")
    http_request = request if isinstance(request, HttpRequest) else None
    q, page_number, base_query, page_url_prefix = parse_grid_query(http_request)
    return http_request, q, page_number, base_query, page_url_prefix


def paginate_grid_items(
    items: list[object],
    *,
    page_number: str | None,
    per_page: int,
) -> tuple[Paginator, Any, list[int], bool, bool]:
    paginator = Paginator(items, per_page)
    page_obj = paginator.get_page(page_number)
    page_numbers, show_first, show_last = pagination_window(paginator, page_obj.number)
    return paginator, page_obj, page_numbers, show_first, show_last


def render_widget_grid(
    *,
    http_request: HttpRequest | None,
    title: str | None,
    empty_label: str,
    base_query: str,
    page_url_prefix: str,
    paginator: Paginator,
    page_obj: Any,
    page_numbers: list[int],
    show_first: bool,
    show_last: bool,
    grid_items: list[dict[str, object]],
) -> str:
    html = render_to_string(
        "core/_widget_grid.html",
        {
            "title": title,
            "empty_label": empty_label,
            "base_query": base_query,
            "page_url_prefix": page_url_prefix,
            "paginator": paginator,
            "page_obj": page_obj,
            "is_paginated": paginator.num_pages > 1,
            "page_numbers": page_numbers,
            "show_first": show_first,
            "show_last": show_last,
            "grid_items": grid_items,
        },
        request=http_request,
    )
    return mark_safe(html)
