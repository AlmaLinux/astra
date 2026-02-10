from typing import Any

from django.db import models
from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils import timezone

from core.models import Membership

register = Library()

def _active_memberships(organization: object) -> list[Membership]:
    """Return active memberships for an organization (expired filtered out)."""
    now = timezone.now()
    return list(
        Membership.objects.select_related("membership_type")
        .filter(target_organization_id=organization.pk)  # type: ignore[union-attr]
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .order_by(
            "membership_type__category__sort_order",
            "membership_type__category__name",
            "membership_type__sort_order",
            "membership_type__code",
            "membership_type__pk",
        )
    )


@register.simple_tag(takes_context=True, name="organization")
def organization_widget(context: Context, organization: object, **kwargs: Any) -> str:
    extra_class = kwargs.get("class", "") or ""
    extra_style = kwargs.get("style", "") or ""
    memberships = _active_memberships(organization)

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
