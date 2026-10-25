import re
from typing import Any

from django.conf import settings
from django.db import migrations

_EXTENDS_LAYOUT_RE = re.compile(r"""{%-?\s*extends\s+["']email-layout["']""")
_FOOTER_BLOCK_RE = re.compile(r"{%-?\s*block\s+footer\b")

_ALMALINUX_LINK = '<br />\n<a href="https://almalinux.org/">almalinux.org</a>'
_COMMITTEE_FOOTER = (
    f"You're receiving this because you review membership requests for the AlmaLinux OS Foundation.{_ALMALINUX_LINK}"
)
_ADMIN_FOOTER = f"You're receiving this because you're an administrator of AlmaLinux Account Services.{_ALMALINUX_LINK}"
# These templates are sent for both personal and organization memberships; only
# organization sends set organization_name, and their recipient may be an
# organization contact without an account, so the account footer would be wrong.
_ORGANIZATION_FOOTER = (
    "{% if organization_name %}You're receiving this as a contact for "
    '<a href="{{ public_base_url }}/organizations/">{{ organization_name }}</a> '
    f"at the AlmaLinux OS Foundation.{_ALMALINUX_LINK}{{% else %}}{{{{ block.super }}}}{{% endif %}}"
)


def _footers(apps: Any) -> dict[str, str]:
    """Map template name to the footer that replaces the layout's default."""

    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    MembershipType = apps.get_model("core", "MembershipType")

    organization_names = {
        settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME,
        settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME,
        settings.MEMBERSHIP_REQUEST_RENEWAL_APPROVED_EMAIL_TEMPLATE_NAME,
        settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
        settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
        settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
        settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
        # Per-type acceptance templates and the variants committees pick by hand.
        *EmailTemplate.objects.filter(name__startswith="membership-request-").values_list("name", flat=True),
        *MembershipType.objects.exclude(acceptance_template__isnull=True).values_list(
            "acceptance_template__name", flat=True
        ),
    }

    return {
        **dict.fromkeys(organization_names, _ORGANIZATION_FOOTER),
        settings.ACCOUNT_DELETION_APPROVED_EMAIL_TEMPLATE_NAME: (
            f"You're receiving this because a request to delete your AlmaLinux account was approved.{_ALMALINUX_LINK}"
        ),
        settings.REGISTRATION_EMAIL_TEMPLATE_NAME: (
            "You're receiving this because this address was used to sign up for an AlmaLinux account. "
            f"If that wasn't you, you can safely ignore this email.{_ALMALINUX_LINK}"
        ),
        settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME: _COMMITTEE_FOOTER,
        settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME: _COMMITTEE_FOOTER,
        settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME: _ADMIN_FOOTER,
        settings.SPONSORSHIP_INVOICE_PAYMENT_DETAILS_ALERT_EMAIL_TEMPLATE_NAME: _ADMIN_FOOTER,
    }


def _footer_block(footer: str) -> str:
    return f"\n\n{{% block footer %}}{footer}{{% endblock %}}"


def add_custom_footers(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    for name, footer in _footers(apps).items():
        for template in EmailTemplate.objects.filter(name=name):
            # Leave templates that don't use the layout, or whose footer staff already customized.
            if not _EXTENDS_LAYOUT_RE.search(template.html_content) or _FOOTER_BLOCK_RE.search(template.html_content):
                continue
            template.html_content += _footer_block(footer)
            template.save(update_fields=["html_content"])


def remove_custom_footers(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    for name, footer in _footers(apps).items():
        block = _footer_block(footer)
        for template in EmailTemplate.objects.filter(name=name, html_content__endswith=block):
            template.html_content = template.html_content.removesuffix(block)
            template.save(update_fields=["html_content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0122_email_template_name_in_context"),
    ]

    operations = [
        migrations.RunPython(add_custom_footers, remove_custom_footers),
    ]
