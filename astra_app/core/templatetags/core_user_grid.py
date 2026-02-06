from __future__ import annotations

from typing import Any, cast

from django.core.paginator import Paginator
from django.http import HttpRequest
from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.backends import FreeIPAGroup, FreeIPAUser
from core.views_utils import _normalize_str

register = Library()


def _get_username_for_sort(user: object) -> str:
    username = getattr(user, "username", None)
    if isinstance(username, str) and username:
        return username.strip().lower()

    get_username = getattr(user, "get_username", None)
    if callable(get_username):
        try:
            return str(get_username()).strip().lower()
        except Exception:
            return ""

    return ""


def _get_full_name_for_filter(user: object) -> str:
    full_name = getattr(user, "full_name", None)
    if isinstance(full_name, str):
        return full_name.strip()
    if full_name is not None:
        try:
            return str(full_name).strip()
        except Exception:
            return ""

    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        try:
            return str(get_full_name()).strip()
        except Exception:
            return ""
    return ""


def _pagination_window(paginator: Paginator, page_number: int) -> tuple[list[int], bool, bool]:
    total_pages = paginator.num_pages
    if total_pages <= 10:
        return list(range(1, total_pages + 1)), False, False

    start = max(1, page_number - 2)
    end = min(total_pages, page_number + 2)
    page_numbers = list(range(start, end + 1))
    show_first = 1 not in page_numbers
    show_last = total_pages not in page_numbers
    return page_numbers, show_first, show_last


def _normalize_members(members_raw: object) -> list[str]:
    if not members_raw:
        return []
    if isinstance(members_raw, str):
        return [members_raw.strip()] if members_raw.strip() else []
    if isinstance(members_raw, list):
        return [str(m).strip() for m in members_raw if str(m).strip()]
    return [str(members_raw).strip()] if str(members_raw).strip() else []


def _normalize_groups(groups_raw: object) -> list[str]:
    return _normalize_members(groups_raw)


@register.simple_tag(takes_context=True, name="user_grid")
def user_grid(context: Context, **kwargs: Any) -> str:
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

    group_arg = kwargs.get("group", None)
    users_arg = kwargs.get("users", None)
    title_arg = kwargs.get("title", None)

    member_manage_enabled = bool(kwargs.get("member_manage_enabled", False))
    member_manage_group_cn_raw = kwargs.get("member_manage_group_cn", None)
    member_manage_group_cn = _normalize_str(member_manage_group_cn_raw) or None

    promote_member_usernames_raw = kwargs.get("promote_member_usernames", None)
    promote_member_usernames: set[str] = set()
    if isinstance(promote_member_usernames_raw, (list, set, tuple)):
        promote_member_usernames = {
            str(u).strip()
            for u in promote_member_usernames_raw
            if str(u).strip()
        }

    muted_usernames_raw = kwargs.get("muted_usernames", None)
    muted_usernames: set[str] = set()
    if isinstance(muted_usernames_raw, (list, set, tuple)):
        muted_usernames = {str(u).strip() for u in muted_usernames_raw if str(u).strip()}

    title = _normalize_str(title_arg) or None

    group_obj: object | None = None
    if group_arg is not None:
        if hasattr(group_arg, "members"):
            group_obj = group_arg
        else:
            group_name = _normalize_str(group_arg)
            if group_name:
                group_obj = FreeIPAGroup.get(group_name)

    usernames_page: list[str] | None = None
    users_page: list[object] | None = None
    items_page: list[dict[str, str]] | None = None

    if group_obj is not None:
        member_groups_raw = cast(Any, group_obj).member_groups if hasattr(group_obj, "member_groups") else []
        member_groups = _normalize_groups(member_groups_raw)
        members = _normalize_members(cast(Any, group_obj).members)

        if q:
            q_lower = q.lower()
            member_groups = [g for g in member_groups if q_lower in g.lower()]
            members = [m for m in members if q_lower in m.lower()]

        def _is_fas_group(cn: str) -> bool:
            obj = FreeIPAGroup.get(cn)
            return bool(obj and obj.fas_group)

        member_groups = [cn for cn in member_groups if _is_fas_group(cn)]

        groups_sorted = sorted(member_groups, key=lambda s: s.lower())
        users_sorted = sorted(members, key=lambda s: s.lower())

        items_all: list[dict[str, str]] = [
            {"kind": "group", "cn": cn} for cn in groups_sorted
        ] + [{"kind": "user", "username": u} for u in users_sorted]

        paginator = Paginator(items_all, per_page)
        page_obj = paginator.get_page(page_number)
        items_page = cast(list[dict[str, str]], page_obj.object_list)

        empty_label = "No members found."
    else:
        users_list: list[object]
        if isinstance(users_arg, list):
            users_list = cast(list[object], users_arg)
        else:
            users_list = cast(list[object], FreeIPAUser.all())

        if q:
            q_lower = q.lower()

            def _matches(user: object) -> bool:
                username = _get_username_for_sort(user)
                if q_lower in username:
                    return True
                full_name = _get_full_name_for_filter(user).lower()
                return q_lower in full_name

            users_list = [u for u in users_list if _matches(u)]

        users_sorted = sorted(users_list, key=_get_username_for_sort)

        paginator = Paginator(users_sorted, per_page)
        page_obj = paginator.get_page(page_number)
        users_page = cast(list[object], page_obj.object_list)

        empty_label = "No users found."

    page_numbers, show_first, show_last = _pagination_window(paginator, page_obj.number)

    template_name = "core/_widget_grid.html"

    grid_items: list[dict[str, object]] = []
    if group_obj is not None:
        grid_items = cast(list[dict[str, object]], items_page or [])
    elif users_page is not None:
        grid_items = [
            {"kind": "user", "username": getattr(u, "username", "")} for u in users_page if getattr(u, "username", "")
        ]
    elif usernames_page is not None:
        grid_items = [{"kind": "user", "username": username} for username in usernames_page if username]

    template_context: dict[str, object] = {
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
        "member_manage_enabled": member_manage_enabled and bool(member_manage_group_cn),
        "member_manage_group_cn": member_manage_group_cn,
        "promote_member_usernames": promote_member_usernames,
        "muted_usernames": muted_usernames,
    }

    html = render_to_string(template_name, template_context, request=http_request)
    return mark_safe(html)
