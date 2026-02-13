from typing import Any, cast

from django.template import Context, Library

from core.templatetags._grid_tag_utils import paginate_grid_items, render_widget_grid, resolve_grid_request
from core.views_utils import _normalize_str

register = Library()


@register.simple_tag(takes_context=True, name="organization_grid")
def organization_grid(context: Context, **kwargs: Any) -> str:
    http_request, q, page_number, base_query, page_url_prefix = resolve_grid_request(context)

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

    paginator, page_obj, page_numbers, show_first, show_last = paginate_grid_items(
        organizations,
        page_number=page_number,
        per_page=per_page,
    )
    orgs_page = cast(list[object], page_obj.object_list)

    grid_items = [{"kind": "organization", "organization": org} for org in orgs_page]

    return render_widget_grid(
        http_request=http_request,
        title=title,
        empty_label=empty_label,
        base_query=base_query,
        page_url_prefix=page_url_prefix,
        paginator=paginator,
        page_obj=page_obj,
        page_numbers=page_numbers,
        show_first=show_first,
        show_last=show_last,
        grid_items=cast(list[dict[str, object]], grid_items),
    )
