from urllib.parse import urlsplit

from django import template
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

    return format_html('<a href="{}">{}</a>', href, raw_value)
