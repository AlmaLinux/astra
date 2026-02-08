from typing import Any, cast

from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.backends import FreeIPAGroup, _clean_str_list
from core.views_utils import _normalize_str

register = Library()


def _get_render_cache(render_context: object, key: str) -> dict:
    """Get or create a dict in the template render context."""
    existing = render_context.get(key)
    if not isinstance(existing, dict):
        existing = {}
        render_context[key] = existing
    return existing


def _get_list_attr(obj: object, name: str) -> list[str]:
    """Extract a list-of-strings attribute from a duck-typed group object.

    Uses getattr: template tag accepts duck-typed objects
    (e.g. tests pass SimpleNamespace).
    """
    return _clean_str_list(getattr(obj, name, None))


@register.simple_tag(takes_context=True, name="group")
def group_widget(context: Context, group: object, **kwargs: Any) -> str:
    raw = _normalize_str(group)
    if not raw:
        return ""

    extra_class = kwargs.get("class", "") or ""
    extra_style = kwargs.get("style", "") or ""

    render_cache = context.render_context
    obj_cache = cast(dict[str, object | None], _get_render_cache(render_cache, "_core_group_widget_cache"))
    count_cache = cast(dict[str, int], _get_render_cache(render_cache, "_core_group_widget_count_cache"))

    group_obj = obj_cache.get(raw)
    if group_obj is None:
        group_obj = FreeIPAGroup.get(raw)
        obj_cache[raw] = group_obj

    def _recursive_usernames(cn: str, *, visited: set[str]) -> set[str]:
        key = cn.strip().lower()
        if not key or key in visited:
            return set()
        visited.add(key)

        obj = obj_cache.get(cn)
        if obj is None:
            obj = FreeIPAGroup.get(cn)
            obj_cache[cn] = obj
        if obj is None:
            return set()

        users: set[str] = set(_get_list_attr(obj, "members"))
        for child_cn in sorted(set(_get_list_attr(obj, "member_groups")), key=str.lower):
            users |= _recursive_usernames(child_cn, visited=visited)
        return users

    member_count = count_cache.get(raw)
    if member_count is None:
        member_count = 0
        if group_obj is not None:
            member_count = len(_recursive_usernames(raw, visited=set()))
        count_cache[raw] = member_count

    description = ""
    if group_obj is not None:
        # Uses getattr: duck-typed interface (tests pass SimpleNamespace)
        desc = getattr(group_obj, "description", "")
        if isinstance(desc, str):
            description = desc.strip()

    html = render_to_string(
        "core/_group_widget.html",
        {
            "cn": raw,
            "member_count": member_count,
            "description": description,
            "extra_class": extra_class,
            "extra_style": extra_style,
        },
        request=context.get("request"),
    )
    return mark_safe(html)
