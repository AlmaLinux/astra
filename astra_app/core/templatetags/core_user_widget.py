from typing import Any, cast

from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.backends import FreeIPAUser
from core.templatetags._user_helpers import try_get_full_name
from core.views_utils import _normalize_str

register = Library()


def _is_avatar_compatible_user(user: object) -> bool:
    # django-avatar needs a "user-like" object. Our FreeIPAUser provides
    # _meta + pk/id which makes it behave like a Django model instance.
    return bool(
        getattr(user, "is_authenticated", False)
        and hasattr(user, "get_username")
        and hasattr(user, "pk")
        and hasattr(user, "_meta")
    )


@register.simple_tag(takes_context=True, name="user")
def user_widget(context: Context, username: object, **kwargs: Any) -> str:
    raw = _normalize_str(username)
    if not raw:
        return ""

    extra_class = kwargs.get("class", "") or ""
    extra_style = kwargs.get("style", "") or ""

    remove_from_group_cn_raw = kwargs.get("remove_from_group_cn")
    remove_from_group_cn = _normalize_str(remove_from_group_cn_raw)

    promote_to_sponsor = bool(kwargs.get("promote_to_sponsor", False))
    demote_from_sponsor = bool(kwargs.get("demote_from_sponsor", False))

    # Per-template-render cache (avoids repeated FreeIPA lookups in a table).
    render_cache = context.render_context
    existing_cache = render_cache.get("_core_user_widget_cache")
    if not isinstance(existing_cache, dict):
        existing_cache = {}
        render_cache["_core_user_widget_cache"] = existing_cache
    cache = cast(dict[str, object | None], existing_cache)

    user_obj: object | None = None

    # If the surrounding template is iterating FreeIPAUser objects already,
    # reuse them without fetching again.
    users_in_context = context.get("users")
    if isinstance(users_in_context, list):
        lookup = cast(dict[str, object] | None, render_cache.get("_core_user_widget_users_lookup"))
        if lookup is None:
            built: dict[str, object] = {}
            for u in users_in_context:
                u_username = getattr(u, "username", None)
                if isinstance(u_username, str) and u_username:
                    built[u_username] = u
            render_cache["_core_user_widget_users_lookup"] = built
            lookup = built
        user_obj = lookup.get(raw)

    if user_obj is None:
        if raw in cache:
            user_obj = cache[raw]
        else:
            user_obj = FreeIPAUser.get(raw)
            cache[raw] = user_obj

    avatar_user = user_obj if user_obj and _is_avatar_compatible_user(user_obj) else None
    full_name = try_get_full_name(user_obj) if user_obj else ""

    html = render_to_string(
        "core/_user_widget.html",
        {
            "username": raw,
            "avatar_user": avatar_user,
            "full_name": full_name,
            "extra_class": extra_class,
            "extra_style": extra_style,
            "remove_from_group_cn": remove_from_group_cn,
            "promote_to_sponsor": promote_to_sponsor,
            "demote_from_sponsor": demote_from_sponsor,
        },
        request=context.get("request"),
    )
    return mark_safe(html)
