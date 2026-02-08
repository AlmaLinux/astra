from typing import Any, cast

from django.core.paginator import Paginator
from django.http import HttpRequest
from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.views_utils import _normalize_str, pagination_window

register = Library()


@register.simple_tag(takes_context=True, name="organization_grid")
def organization_grid(context: Context, **kwargs: Any) -> str:
    request = context.get("request")
    http_request = request if isinstance(request, HttpRequest) else None

    q = ""
    page_number: str | None = None
    base_query = ""
    page_url_prefix = "?page="
    if http_request is not None:
        q = _normalize_str(http_request.GET.get("q"))
        page_number = _normalize_str(http_request.GET.get("page")) or None

        params = http_request.GET.copy()
        params.pop("page", None)
        base_query = params.urlencode()
        page_url_prefix = f"?{base_query}&page=" if base_query else "?page="

    per_page = 28

    orgs_arg = kwargs.get("organizations")
    title_arg = kwargs.get("title")
    empty_label_arg = kwargs.get("empty_label")

    title = _normalize_str(title_arg) or None
    empty_label = _normalize_str(empty_label_arg) or "No organizations found."

    organizations: list[object]

    if orgs_arg is None:
        organizations = []
    elif isinstance(orgs_arg, list):
        organizations = cast(list[object], orgs_arg)
    else:
        # Accept QuerySet-like iterables.
        try:
            organizations = list(cast(Any, orgs_arg))
        except Exception:
            organizations = []

    if q:
        q_lower = q.lower()

        def _matches(org: object) -> bool:
            if hasattr(org, "name"):
                try:
                    return q_lower in str(cast(Any, org).name).lower()
                except Exception:
                    return False
            return False

        organizations = [o for o in organizations if _matches(o)]

    paginator = Paginator(organizations, per_page)
    page_obj = paginator.get_page(page_number)
    orgs_page = cast(list[object], page_obj.object_list)

    page_numbers, show_first, show_last = pagination_window(paginator, page_obj.number)

    grid_items = [{"kind": "organization", "organization": org} for org in orgs_page]

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
