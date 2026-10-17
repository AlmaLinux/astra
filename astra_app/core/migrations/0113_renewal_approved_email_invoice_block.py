from django.db import migrations

TEMPLATE_NAME = "membership-renewal-approved"

# Individual renewals share this template; the block only renders for sponsors, who get the invoice attached.
TEXT_BLOCK = (
    "{% if invoice_url %}"
    "**Invoice**\n\n"
    "The invoice for your sponsorship renewal ({{ invoice_number }}) is attached to this email. "
    "If the attachment is missing, you can download it here: {{ invoice_url }}\n\n"
    "{% endif %}"
)
HTML_BLOCK = (
    "{% if invoice_url %}"
    "<p><strong>Invoice</strong></p>\n"
    "<p>The invoice for your sponsorship renewal ({{ invoice_number }}) is attached to this email. "
    "If the attachment is missing, you can "
    "<a href=\"{{ invoice_url }}\">download the invoice here</a>.</p>\n"
    "{% endif %}"
)


def _insert_block(source: str, *, anchor: str, block: str) -> str:
    # The seeded template closes with "Thanks again..."; operators may have edited that away.
    if anchor in source:
        return source.replace(anchor, f"{block}{anchor}", 1)
    return f"{source}{block}"


def add_invoice_block(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    for template in EmailTemplate.objects.filter(name=TEMPLATE_NAME):
        if "invoice_url" in (template.content or "") + (template.html_content or ""):
            continue
        template.content = _insert_block(template.content or "", anchor="Thanks again", block=TEXT_BLOCK)
        template.html_content = _insert_block(template.html_content or "", anchor="<p>Thanks again", block=HTML_BLOCK)
        template.save(update_fields=["content", "html_content"])


def remove_invoice_block(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    for template in EmailTemplate.objects.filter(name=TEMPLATE_NAME):
        template.content = (template.content or "").replace(TEXT_BLOCK, "")
        template.html_content = (template.html_content or "").replace(HTML_BLOCK, "")
        template.save(update_fields=["content", "html_content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0112_membership_created_at_default"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(add_invoice_block, remove_invoice_block),
    ]
