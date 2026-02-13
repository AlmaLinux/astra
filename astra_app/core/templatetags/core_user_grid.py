from typing import Any, cast

from django.template import Context, Library

from core.backends import FreeIPAGroup, FreeIPAUser, _clean_str_list
from core.templatetags._grid_tag_utils import paginate_grid_items, render_widget_grid, resolve_grid_request
from core.templatetags._user_helpers import try_get_full_name
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


def _normalize_groups(groups_raw: object) -> list[str]:
    return _clean_str_list(groups_raw)


@register.simple_tag(takes_context=True, name="user_grid")
def user_grid(context: Context, **kwargs: Any) -> str:
    http_request, q, page_number, base_query, page_url_prefix = resolve_grid_request(context)

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

    users_page: list[object] | None = None
    items_page: list[dict[str, str]] | None = None

    if group_obj is not None:
        member_groups_raw = cast(Any, group_obj).member_groups if hasattr(group_obj, "member_groups") else []
        member_groups = _normalize_groups(member_groups_raw)
        members = _clean_str_list(cast(Any, group_obj).members)

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

        paginator, page_obj, page_numbers, show_first, show_last = paginate_grid_items(
            cast(list[object], items_all),
            page_number=page_number,
            per_page=per_page,
        )
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
                full_name = try_get_full_name(user).lower()
                return q_lower in full_name

            users_list = [u for u in users_list if _matches(u)]

        users_sorted = sorted(users_list, key=_get_username_for_sort)

        paginator, page_obj, page_numbers, show_first, show_last = paginate_grid_items(
            users_sorted,
            page_number=page_number,
            per_page=per_page,
        )
        users_page = cast(list[object], page_obj.object_list)

        empty_label = "No users found."

    effective_manage = member_manage_enabled and bool(member_manage_group_cn)

    grid_items: list[dict[str, object]] = []
    if group_obj is not None:
        grid_items = cast(list[dict[str, object]], items_page or [])
        for item in grid_items:
            if item.get("kind") != "user":
                continue
            username = str(item.get("username", ""))
            is_muted = username in muted_usernames
            item["remove_from_group_cn"] = member_manage_group_cn if effective_manage else ""
            item["promote_to_sponsor"] = effective_manage and username in promote_member_usernames
            item["extra_class"] = "text-muted" if is_muted else ""
            item["extra_style"] = "opacity:0.55;" if is_muted else ""
    elif users_page is not None:
        for u in users_page:
            # user-like objects may be FreeIPAUser, SimpleNamespace, etc.
            username = getattr(u, "username", "")
            if not username:
                continue
            is_muted = username in muted_usernames
            grid_items.append({
                "kind": "user",
                "username": username,
                "remove_from_group_cn": member_manage_group_cn if effective_manage else "",
                "promote_to_sponsor": effective_manage and username in promote_member_usernames,
                "extra_class": "text-muted" if is_muted else "",
                "extra_style": "opacity:0.55;" if is_muted else "",
            })

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
        grid_items=grid_items,
    )
