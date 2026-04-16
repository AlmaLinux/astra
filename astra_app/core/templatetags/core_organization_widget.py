from typing import Any

from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from core.models import Membership, Organization

register = Library()


def _should_link_to_detail(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


@register.simple_tag(takes_context=True, name="organization")
def organization_widget(context: Context, organization: Organization, **kwargs: Any) -> str:
    extra_class = kwargs.get("class", "") or ""
    extra_style = kwargs.get("style", "") or ""
    link_to_detail = _should_link_to_detail(kwargs.get("link_to_detail", True))
    if "organization_memberships_by_id" not in context:
        raise RuntimeError("organization widget requires organization_memberships_by_id in template context")

    memberships_by_id = context["organization_memberships_by_id"]
    if organization.pk not in memberships_by_id:
        raise RuntimeError(
            "organization widget requires organization_memberships_by_id entry for each rendered organization"
        )
    memberships: list[Membership] = memberships_by_id[organization.pk]

    html = render_to_string(
        "core/_organization_widget.html",
        {
            "organization": organization,
            "memberships": memberships,
            "extra_class": extra_class,
            "extra_style": extra_style,
            "link_to_detail": link_to_detail,
        },
    )
    return mark_safe(html)
