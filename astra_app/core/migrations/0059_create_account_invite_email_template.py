from __future__ import annotations

from django.db import migrations

from core.migration_helpers.email_template_text import text_from_html


def add_account_invite_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    html_content = (
        "<p>Hello{% if full_name %} {{ full_name }}{% endif %},</p>\n"
        "<p>The AlmaLinux OS Foundation is expanding how members participate in elections, "
        "receive official communications, and manage membership details.</p>\n"
        "<p>To continue participating, you will need an AlmaLinux account (managed through AlmaLinux Accounts).</p>\n"
        "<p>You can create your AlmaLinux account here:</p>\n"
        "<p><a href=\"{{ register_url }}\">Create your AlmaLinux account</a></p>\n"
        "<p>Already have an AlmaLinux account? <a href=\"{{ login_url }}\">Sign in</a>.</p>\n"
        "<p>If you have any questions or need assistance, reply to this email and we'll be happy to help.</p>\n"
        "<p><em>The AlmaLinux Team</em></p>\n"
    )

    EmailTemplate.objects.update_or_create(
        name="account-invite",
        defaults={
            "description": "Invite someone to create an AlmaLinux account",
            "subject": "Create your AlmaLinux account",
            "html_content": html_content,
            "content": text_from_html(html_content),
        },
    )


def noop_reverse(apps, schema_editor) -> None:
    # Keep templates on rollback to avoid losing admin edits.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0058_create_membership_embargoed_members_email_template"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            add_account_invite_template,
            reverse_code=noop_reverse,
        ),
    ]
