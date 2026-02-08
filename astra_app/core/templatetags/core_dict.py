from collections.abc import Mapping
from typing import Any

from django import template

register = template.Library()


@register.filter(name="dict_get")
def dict_get(mapping: Mapping[str, Any] | None, key: str) -> Any:  # noqa: ANN401
    """Safely get a value from a dict-like object.

    Django templates can raise VariableDoesNotExist when trying to access missing
    keys via dot-lookup inside tags like `{% with %}`. This filter keeps template
    partials resilient across bound/unbound forms.
    """

    if mapping is None:
        return ""
    return mapping.get(key, "")


@register.simple_tag
def make_presets(*args: str) -> list[dict[str, str]]:
    """Build a list of ``{label, value}`` dicts from alternating arguments.

    Usage in templates::

        {% load core_dict %}
        {% make_presets "Label A" "value-a" "Label B" "value-b" as my_presets %}

    The resulting list can be passed to included partials that accept a
    ``presets`` context variable (e.g. ``_modal_preset_textarea.html``).
    """
    return [
        {"label": args[i], "value": args[i + 1]}
        for i in range(0, len(args) - 1, 2)
    ]
