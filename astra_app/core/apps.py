from copy import deepcopy
from typing import Any, override

from django.apps import AppConfig


def _patch_jazzmin_format_html() -> None:
    """Patch Jazzmin's template tag module for Django compatibility.

    Newer Django versions raise `TypeError` if `format_html()` is called without
    args/kwargs. Jazzmin's paginator tag calls `format_html(html_str)`.
    We replace the module-local `format_html` with a wrapper that treats a
    no-arg call as already-formatted HTML.
    """

    try:
        import jazzmin.templatetags.jazzmin as jazzmin_tags
    except ImportError:
        return

    # Idempotent.
    if getattr(jazzmin_tags, "_core_format_html_patched", False):
        return

    try:
        from django.utils.html import format_html as django_format_html
        from django.utils.safestring import mark_safe
    except ImportError:
        return

    def compat_format_html(format_string, *args, **kwargs):
        if not args and not kwargs:
            return mark_safe(format_string)
        return django_format_html(format_string, *args, **kwargs)

    jazzmin_tags.format_html = compat_format_html
    jazzmin_tags._core_format_html_patched = True


def _normalize_model_str(value: object) -> str:
    return str(value or "").strip().lower()


def _group_app_label(name: str) -> str:
    sanitized = [ch.lower() if ch.isalnum() else "_" for ch in name]
    return "".join(sanitized).strip("_") or "custom_group"


def _apply_jazzmin_model_groups(
    *,
    menu: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    default_icon: str,
    fallback_menu: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not groups:
        return menu

    grouped_menu = deepcopy(menu)
    synthetic_sections: list[dict[str, Any]] = []
    fallback_lookup: dict[str, dict[str, Any]] = {}

    if isinstance(fallback_menu, list):
        for app in fallback_menu:
            app_models = app.get("models") if isinstance(app, dict) else None
            if not isinstance(app_models, list):
                continue
            for model in app_models:
                if not isinstance(model, dict):
                    continue
                model_key = _normalize_model_str(model.get("model_str"))
                if model_key and model_key not in fallback_lookup:
                    fallback_lookup[model_key] = deepcopy(model)

    def pop_model_entry(model_str: str) -> dict[str, Any] | None:
        for app in grouped_menu:
            app_models = app.get("models")
            if not isinstance(app_models, list):
                continue
            for idx, model in enumerate(app_models):
                if not isinstance(model, dict):
                    continue
                candidate = _normalize_model_str(model.get("model_str"))
                if candidate == model_str:
                    return app_models.pop(idx)
        return None

    for group in groups:
        group_name_value = group.get("name")
        if not isinstance(group_name_value, str) or not group_name_value.strip():
            continue

        configured_models = group.get("models")
        if not isinstance(configured_models, list):
            continue

        grouped_models: list[dict[str, Any]] = []
        for raw_model in configured_models:
            model_str = _normalize_model_str(raw_model)
            if not model_str:
                continue
            model_entry = pop_model_entry(model_str)
            if model_entry is None:
                fallback_entry = fallback_lookup.get(model_str)
                if fallback_entry is not None:
                    model_entry = deepcopy(fallback_entry)
            if model_entry is not None:
                grouped_models.append(model_entry)

        if not grouped_models:
            continue

        group_icon = default_icon
        group_icon_value = group.get("icon")
        if isinstance(group_icon_value, str) and group_icon_value.strip():
            group_icon = group_icon_value.strip()

        group_name = group_name_value.strip()
        synthetic_sections.append(
            {
                "name": group_name,
                "app_label": _group_app_label(group_name),
                "app_url": "",
                "has_module_perms": True,
                "models": grouped_models,
                "icon": group_icon,
            }
        )

    remaining_sections: list[dict[str, Any]] = []
    for app in grouped_menu:
        app_models = app.get("models")
        if isinstance(app_models, list) and app_models:
            remaining_sections.append(app)

    return synthetic_sections + remaining_sections


def _patch_jazzmin_side_menu_model_groups() -> None:
    """Support custom model groups in Jazzmin side menu via JAZZMIN_SETTINGS['model_groups']."""

    try:
        import jazzmin.templatetags.jazzmin as jazzmin_tags
    except ImportError:
        return

    if hasattr(jazzmin_tags, "_core_model_groups_patched") and jazzmin_tags._core_model_groups_patched:
        return

    original_get_side_menu = jazzmin_tags.get_side_menu

    def grouped_get_side_menu(context, using: str = "available_apps"):
        menu = original_get_side_menu(context, using=using)
        options = jazzmin_tags.get_settings()
        configured_groups = options.get("model_groups", [])
        if not isinstance(configured_groups, list) or not configured_groups:
            return menu

        groups: list[dict[str, Any]] = [entry for entry in configured_groups if isinstance(entry, dict)]
        if not groups:
            return menu

        default_icon_value = options.get("default_icon_parents", "fas fa-cog")
        default_icon = str(default_icon_value or "fas fa-cog")

        fallback_menu: list[dict[str, Any]] | None = None
        if using != "available_apps":
            available_apps = context.get("available_apps")
            if isinstance(available_apps, list):
                fallback_menu = original_get_side_menu(context, using="available_apps")

        return _apply_jazzmin_model_groups(
            menu=menu,
            groups=groups,
            default_icon=default_icon,
            fallback_menu=fallback_menu,
        )

    grouped_get_side_menu.__name__ = "get_side_menu"
    jazzmin_tags.register.simple_tag(takes_context=True, name="get_side_menu")(grouped_get_side_menu)
    jazzmin_tags._core_model_groups_patched = True


def _patch_django_avatar_get_user() -> None:
    """Make django-avatar template tags compatible with FreeIPA-backed users.

    django-avatar's template tags call `avatar.utils.get_user()` which expects
    either a real Django AUTH_USER_MODEL instance or a string/PK descriptor.

    This project uses non-persistent FreeIPA-backed user objects for
    `request.user`, so we treat any object that looks like an authenticated
    user (has `is_authenticated` + `get_username`) as already-resolved.

    This keeps `{% avatar request.user %}` and `{% avatar_url request.user %}`
    working without forcing DB-backed users.
    """

    try:
        import avatar.templatetags.avatar_tags as avatar_tags
        import avatar.utils as avatar_utils
    except ImportError:
        return

    # Idempotent.
    if getattr(avatar_utils, "_core_get_user_patched", False):
        return

    original_get_user = avatar_utils.get_user

    def compat_get_user(userdescriptor):
        # Unwrap SimpleLazyObject when it has already been evaluated.
        # If it is still the `empty` sentinel, leave it alone and let the
        # attribute checks below trigger evaluation safely.
        try:
            from django.utils.functional import empty

            wrapped = getattr(userdescriptor, "_wrapped", None)
            if wrapped is not None and wrapped is not empty and wrapped is not userdescriptor:
                userdescriptor = wrapped
        except Exception:
            pass

        # Our FreeIPA user objects are already resolved and behave like users.
        if getattr(userdescriptor, "is_authenticated", False) and hasattr(userdescriptor, "get_username"):
            return userdescriptor

        return original_get_user(userdescriptor)

    avatar_utils.get_user = compat_get_user
    # avatar_tags imports get_user into module scope; patch that reference too.
    avatar_tags.get_user = compat_get_user
    avatar_utils._core_get_user_patched = True


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    @override
    def ready(self) -> None:
        _patch_jazzmin_format_html()
        _patch_jazzmin_side_menu_model_groups()
        _patch_django_avatar_get_user()
        __import__("core.checks_membership")
        __import__("core.ses_signals")
