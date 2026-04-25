from django.http import HttpRequest

from core.views_utils import build_page_url_prefix, paginate_and_build_context


def serialize_pagination(page_ctx: dict[str, object]) -> dict[str, object]:
    paginator = page_ctx["paginator"]
    page_obj = page_ctx["page_obj"]
    page_numbers = page_ctx["page_numbers"]
    show_first = page_ctx["show_first"]
    show_last = page_ctx["show_last"]

    return {
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "page_numbers": page_numbers,
        "show_first": show_first,
        "show_last": show_last,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page_number": page_obj.previous_page_number() if page_obj.has_previous() else None,
        "next_page_number": page_obj.next_page_number() if page_obj.has_next() else None,
        "start_index": page_obj.start_index() if paginator.count else 0,
        "end_index": page_obj.end_index() if paginator.count else 0,
    }


def paginate_detail_items(
    request: HttpRequest,
    items: list[object],
    *,
    page_param: str = "page",
    per_page: int = 30,
) -> tuple[list[object], dict[str, object]]:
    page_number = str(request.GET.get(page_param) or "").strip() or None
    _, page_prefix = build_page_url_prefix(request.GET, page_param=page_param)
    page_ctx = paginate_and_build_context(items, page_number, per_page, page_url_prefix=page_prefix)
    return list(page_ctx["page_obj"].object_list), page_ctx