from django.db import migrations

REMINDER_TEMPLATE_NAMES = ("organization-sponsorship-expiring-soon", "organization-sponsorship-expired")

REMINDER_TEXT_BLOCK = (
    "{% if invoice_url %}"
    "---\n"
    "Invoice\n\n"
    "The invoice for your sponsorship renewal ({{ invoice_number }}) is attached to this email. "
    "If the attachment is missing, you can download it here:\n\n"
    "  {{ invoice_url }}\n\n"
    "{% endif %}"
)
REMINDER_HTML_BLOCK = (
    "{% if invoice_url %}"
    "<hr>"
    "<h3>Invoice</h3>"
    "<p>The invoice for your sponsorship renewal ({{ invoice_number }}) is attached to this email. "
    "If the attachment is missing, you can "
    "<a href=\"{{ invoice_url }}\">download the invoice here</a>.</p>"
    "{% endif %}"
)

WELCOME_TEXT_BLOCK = (
    "{% if invoice_url %}"
    "**Invoice**\n\n"
    "The invoice for your sponsorship ({{ invoice_number }}) is attached to this email. "
    "If the attachment is missing, you can download it here: {{ invoice_url }}\n\n"
    "{% endif %}"
)
WELCOME_HTML_BLOCK = (
    "{% if invoice_url %}"
    "<p><strong>Invoice</strong></p>\n"
    "<p>The invoice for your sponsorship ({{ invoice_number }}) is attached to this email. "
    "If the attachment is missing, you can "
    "<a href=\"{{ invoice_url }}\">download the invoice here</a>.</p>\n"
    "{% endif %}"
)


def _templates_and_blocks(apps):
    """Yield (templates, text block, html block, text anchor, html anchor).

    Each block goes in before the seeded closing note; operators may have edited
    that away, in which case the block is appended.
    """
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    MembershipType = apps.get_model("core", "MembershipType")

    # Reminders close with an "If you have already extended/renewed" note.
    yield (
        EmailTemplate.objects.filter(name__in=REMINDER_TEMPLATE_NAMES),
        REMINDER_TEXT_BLOCK,
        REMINDER_HTML_BLOCK,
        "If you have already",
        "<p>If you have already",
    )
    # Welcome emails for organization memberships close with "Thanks again, and welcome...".
    welcome_template_ids = MembershipType.objects.filter(
        category__is_organization=True,
        acceptance_template__isnull=False,
    ).values_list("acceptance_template_id", flat=True)
    yield (
        EmailTemplate.objects.filter(pk__in=welcome_template_ids),
        WELCOME_TEXT_BLOCK,
        WELCOME_HTML_BLOCK,
        "Thanks again",
        "<p>Thanks again",
    )


def _insert_block(source: str, *, anchor: str, block: str) -> str:
    if anchor in source:
        return source.replace(anchor, f"{block}{anchor}", 1)
    return f"{source}{block}"


def add_invoice_blocks(apps, schema_editor) -> None:
    for templates, text_block, html_block, text_anchor, html_anchor in _templates_and_blocks(apps):
        for template in templates:
            if "invoice_url" in (template.content or "") + (template.html_content or ""):
                continue
            template.content = _insert_block(template.content or "", anchor=text_anchor, block=text_block)
            template.html_content = _insert_block(template.html_content or "", anchor=html_anchor, block=html_block)
            template.save(update_fields=["content", "html_content"])


def remove_invoice_blocks(apps, schema_editor) -> None:
    for templates, text_block, html_block, _text_anchor, _html_anchor in _templates_and_blocks(apps):
        for template in templates:
            template.content = (template.content or "").replace(text_block, "")
            template.html_content = (template.html_content or "").replace(html_block, "")
            template.save(update_fields=["content", "html_content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0102_organization_billing_notes_invoice_token"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(add_invoice_blocks, remove_invoice_blocks),
    ]
