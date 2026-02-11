from typing import Any

from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.membership import get_valid_memberships

register = Library()

@register.simple_tag(takes_context=True, name="organization")
def organization_widget(context: Context, organization: object, **kwargs: Any) -> str:
    extra_class = kwargs.get("class", "") or ""
    extra_style = kwargs.get("style", "") or ""
    memberships = get_valid_memberships(organization=organization)

    html = render_to_string(
        "core/_organization_widget.html",
        {
            "organization": organization,
            "memberships": memberships,
            "extra_class": extra_class,
            "extra_style": extra_style,
        },
        request=context.get("request"),
    )
    return mark_safe(html)
