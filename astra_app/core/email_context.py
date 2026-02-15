import html
from typing import TYPE_CHECKING

from django.conf import settings
from django.urls import reverse
from django.utils.safestring import SafeString, mark_safe

from core.backends import FreeIPAUser
from core.public_urls import build_public_absolute_url, normalize_public_base_url

if TYPE_CHECKING:
    from core.models import Organization


def user_email_context(*, username: str) -> dict[str, str]:
    """Return canonical user variables for templated emails.

    The email templates assume these variables exist, even if blank:
    - username
    - first_name
    - last_name
    - full_name
    - email

    We intentionally do not expose `displayname` to templates.
    """

    normalized_username = str(username or "").strip()
    if not normalized_username:
        return {"username": "", "first_name": "", "last_name": "", "full_name": "", "email": ""}

    try:
        user = FreeIPAUser.get(normalized_username)
    except Exception:
        user = None
    if user is None:
        return {
            "username": normalized_username,
            "first_name": "",
            "last_name": "",
            "full_name": normalized_username,
            "email": "",
        }

    return user_email_context_from_user(user=user)


def user_email_context_from_user(*, user: FreeIPAUser) -> dict[str, str]:
    return {
        "username": str(user.username or ""),
        "first_name": str(user.first_name or ""),
        "last_name": str(user.last_name or ""),
        "full_name": str(user.full_name or ""),
        "email": str(user.email or ""),
    }


def organization_email_context_from_organization(*, organization: Organization) -> dict[str, str]:
    """Return canonical organization variables for sponsor-facing templated emails.

    The email templates assume these variables exist, even if blank:
    - business_contact_name, business_contact_email
    - pr_marketing_contact_name, pr_marketing_contact_email
    - technical_contact_name, technical_contact_email
    """

    return {
        "business_contact_name": str(organization.business_contact_name or ""),
        "business_contact_email": str(organization.business_contact_email or ""),
        "pr_marketing_contact_name": str(organization.pr_marketing_contact_name or ""),
        "pr_marketing_contact_email": str(organization.pr_marketing_contact_email or ""),
        "technical_contact_name": str(organization.technical_contact_name or ""),
        "technical_contact_email": str(organization.technical_contact_email or ""),
    }


def organization_sponsor_email_context(*, organization: Organization) -> dict[str, str]:
    """Return sponsor email context: org contact fields + representative user variables."""

    representative_username = str(organization.representative or "").strip()
    if representative_username:
        try:
            representative = FreeIPAUser.get(representative_username)
        except Exception:
            representative = None
        representative_context = (
            user_email_context_from_user(user=representative)
            if representative is not None
            else user_email_context(username=representative_username)
        )
    else:
        representative_context = user_email_context(username="")

    return organization_email_context_from_organization(organization=organization) | representative_context


def membership_committee_email_context() -> dict[str, str]:
    return {
        "membership_committee_email": str(settings.MEMBERSHIP_COMMITTEE_EMAIL or "").strip(),
    }


def election_committee_email_context() -> dict[str, str]:
    return {
        "election_committee_email": str(settings.ELECTION_COMMITTEE_EMAIL or "").strip(),
    }


def system_email_context() -> dict[str, str]:
    public_base_url = normalize_public_base_url(settings.PUBLIC_BASE_URL)
    register_url = build_public_absolute_url(reverse("register"), on_missing="empty")
    login_url = build_public_absolute_url(reverse("login"), on_missing="empty")
    return {
        "public_base_url": public_base_url,
        "register_url": register_url,
        "login_url": login_url,
    }


def freeform_message_email_context(*, key: str, value: str) -> dict[str, str | SafeString]:
        """Return email context values for freeform user-provided text.

        We need two variants:
        - `<key>_text`: rendered into text/plain without HTML entity escaping.
        - `<key>_html`: rendered into text/html safely, without showing HTML entities
            for common punctuation like apostrophes.

        The base `<key>` is retained for backwards compatibility with existing
        templates.
        """

        normalized = html.unescape(str(value or "").strip())
        html_escaped = html.escape(normalized, quote=False)

        return {
                key: normalized,
                f"{key}_text": mark_safe(normalized),
                f"{key}_html": mark_safe(html_escaped),
        }
