from urllib.parse import urlencode, urlsplit

from django import template
from django.urls import reverse
from django.utils.html import escape, format_html

from core.forms_membership import MembershipRequestForm

register = template.Library()


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


@register.filter(name="membership_response_value")
def membership_response_value(value: object, question: object) -> str:
    """Render a membership-request response value.

    If the question is a URL-typed question (currently only Mirror: Domain and Pull request),
    render a clickable link. For backward compatibility with older stored responses, bare
    domains are treated as https URLs when linking.
    """

    raw_value = str(value or "").strip()
    if not raw_value:
        return ""

    question_name = str(question or "").strip()
    spec = MembershipRequestForm._question_spec_by_name().get(question_name)
    if spec is None or spec.answer_kind.value != "url":
        return escape(raw_value)

    href = raw_value
    if "://" not in href:
        href = f"https://{href}"

    if not _is_http_url(href):
        return escape(raw_value)

    link_html = format_html('<a href="{}">{}</a>', href, raw_value)
    if question_name != "Domain":
        return link_html

    badge_url = f"{reverse('mirror-badge-svg')}?{urlencode({'url': href})}"
    status_url = reverse("mirror-badge-status")
    badge_html = format_html(
        '<span class="d-inline-flex align-items-center ml-2" style="vertical-align: middle;" data-mirror-badge-container data-mirror-badge-status-endpoint-url="{}">'
        '<span class="spinner-border spinner-border-sm text-muted" data-mirror-badge-loading role="status" aria-hidden="true"></span>'
        '<img class="d-none ml-1" data-mirror-badge-img src="{}" alt="Mirror status" title="Mirror status: loading..." loading="eager" decoding="async" />'
        "</span>",
        status_url,
        badge_url,
    )
    return format_html("{}{}", link_html, badge_html)
