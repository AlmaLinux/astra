from typing import Any

from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

register = Library()

_TIER_BADGE_CLASSES = {
    "platinum": "badge-primary",
    "gold": "badge-warning",
    "silver": "badge-secondary",
    "ruby": "badge-danger",
}


def _sponsorship_info(organization: object) -> dict[str, str]:
    """Derive all sponsorship display metadata in one pass.

    Template tags are intentionally duck-typed; the object may be a real
    Organization model or a lightweight stub from tests.
    """
    empty: dict[str, str] = {"label": "", "tier": "", "badge_class": "badge-info", "pill_text": ""}

    # Duck-typed: object may lack membership_level entirely.
    if not hasattr(organization, "membership_level"):
        return empty

    level = organization.membership_level
    if level is None:
        return empty

    # --- label (from name) ---
    label = ""
    if hasattr(level, "name"):
        try:
            label = str(level.name or "").strip()
        except Exception:
            pass

    # --- tier (from code, fallback to first word of label) ---
    tier = ""
    if hasattr(level, "code"):
        try:
            tier = str(level.code or "").strip().replace("_", " ").title()
        except Exception:
            pass
    if not tier and label:
        parts = label.split()
        tier = parts[0].strip().title() if parts else ""

    badge_class = _TIER_BADGE_CLASSES.get(tier.strip().lower(), "badge-info")

    # --- pill_text (label with trailing "Member" / "Sponsor Member" stripped) ---
    pill_text = ""
    if label:
        low = label.lower()
        if low.endswith(" sponsor member"):
            pill_text = label[: -len(" member")]
        elif low.endswith(" member"):
            pill_text = label[: -len(" member")]
        else:
            pill_text = label

    return {"label": label, "tier": tier, "badge_class": badge_class, "pill_text": pill_text}


@register.simple_tag(takes_context=True, name="organization")
def organization_widget(context: Context, organization: object, **kwargs: Any) -> str:
    extra_class = kwargs.get("class", "") or ""
    extra_style = kwargs.get("style", "") or ""
    info = _sponsorship_info(organization)

    html = render_to_string(
        "core/_organization_widget.html",
        {
            "organization": organization,
            "sponsorship_label": info["label"],
            "sponsorship_tier": info["tier"],
            "sponsorship_badge_class": info["badge_class"],
            "sponsorship_pill_text": info["pill_text"],
            "extra_class": extra_class,
            "extra_style": extra_style,
        },
        request=context.get("request"),
    )
    return mark_safe(html)
