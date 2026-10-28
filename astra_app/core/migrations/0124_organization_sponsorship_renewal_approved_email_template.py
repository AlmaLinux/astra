import re
from importlib import import_module

from django.db import migrations

TEMPLATE_NAME = "organization-sponsorship-renewal-approved"
INDIVIDUAL_RENEWAL_TEMPLATE_NAME = "membership-renewal-approved"

# Sponsor renewals used to share the individual renewal email, which carried their
# invoice. Renewals are approved once paid, so that block is no longer needed there.
# Matched loosely, since staff may have reworded the block 0113 added.
_INVOICE_BLOCK_RE = re.compile(r"{%\s*if invoice_url\s*%}.*?{%\s*endif\s*%}", re.DOTALL)
_renewal_invoice_block = import_module("core.migrations.0113_renewal_approved_email_invoice_block")


def create_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.update_or_create(
        name=TEMPLATE_NAME,
        defaults={
            "description": "Approval for an organization sponsorship renewal",
            "subject": "The {{ membership_type }} membership for {{ organization_name }} has been renewed",
            "content": (
                "Hello {{ full_name|default:organization_name }},\n\n"
                "We are happy to share that the {{ membership_type }} membership for **{{ organization_name }}** "
                "has been renewed!\n\n"
                "Thank you for continuing to back the only enterprise Linux distribution built for the community, by the "
                "community. Your sponsorship helps us continue to build infrastructure and pay for the operational "
                "needs of the foundation, while building our reach and educating more people about the ways that "
                "we can support them.\n\n"
                "**Show it off!**\n\n"
                "If you haven't already, please join our chat server at "
                "[chat.almalinux.org](https://chat.almalinux.org/). If you want to sport your very own AlmaLinux "
                "gear, head on over to [shop.almalinux.org](https://shop.almalinux.org) to pick up some goodies! "
                "We have hoodies, shirts, mugs, phone cases, and much more.\n\n"
                "If you have any questions about your sponsorship, just reply to this email.\n\n"
                "Thanks again for being part of the AlmaLinux OS Foundation.\n\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                '{% extends "email-layout" %}{% block content %}\n'
                "<p>Hello {{ full_name|default:organization_name }},</p>\n"
                "<p>We are happy to share that the {{ membership_type }} membership for "
                "<strong>{{ organization_name }}</strong> has been renewed!</p>\n"
                "<p>Thank you for continuing to back the only enterprise Linux distribution built for the community, by the "
                "community. Your sponsorship helps us continue to build infrastructure and pay for the operational "
                "needs of the foundation, while building our reach and educating more people about the ways that "
                "we can support them.</p>\n"
                "<p><strong>Show it off!</strong></p>\n"
                '<p>If you haven\'t already, please join our chat server at <a href="https://chat.almalinux.org/">'
                "chat.almalinux.org</a>. If you want to sport your very own AlmaLinux gear, head on over to "
                '<a href="https://shop.almalinux.org">shop.almalinux.org</a> to pick up some goodies! '
                "We have hoodies, shirts, mugs, phone cases, and much more.</p>\n"
                "<p>If you have any questions about your sponsorship, just reply to this email.</p>\n"
                "<p>Thanks again for being part of the AlmaLinux OS Foundation.</p>\n"
                "<p><em>The AlmaLinux Team</em></p>\n"
                "{% endblock %}\n\n"
                "{% block footer %}You're receiving this as a contact for "
                '<a href="{{ public_base_url }}/organizations/">{{ organization_name }}</a> '
                "at the AlmaLinux OS Foundation.<br />\n"
                '<a href="https://almalinux.org/">almalinux.org</a>{% endblock %}'
            ),
        },
    )


def remove_invoice_block(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    for template in EmailTemplate.objects.filter(name=INDIVIDUAL_RENEWAL_TEMPLATE_NAME):
        template.content = _INVOICE_BLOCK_RE.sub("", template.content or "")
        template.html_content = _INVOICE_BLOCK_RE.sub("", template.html_content or "")
        template.save(update_fields=["content", "html_content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0123_email_layout_custom_footers"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        # Unapplied as a no-op: deleting through the historical models fails when
        # migrating back (Email's FK points to a stale EmailTemplate class), and an
        # unused template is harmless.
        migrations.RunPython(create_template, migrations.RunPython.noop),
        migrations.RunPython(remove_invoice_block, _renewal_invoice_block.add_invoice_block),
    ]
