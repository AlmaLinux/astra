from __future__ import annotations

from typing import Any

from django.db import migrations


def create_account_deletion_approved_email_template(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        name="account-deletion-approved",
        defaults={
            "description": "Notification sent after an account deletion request is approved and executed",
            "subject": "Your AlmaLinux account has been deleted",
            "content": (
                "Hi {{ full_name }},\n\n"
                "Your account deletion request submitted on {{ requested_at_utc }} has been approved, and your AlmaLinux account has now been deleted.\n\n"
                "If you did not request this change, contact the AlmaLinux accounts team immediately.\n\n"
                "If you would like to come back in the future, you are always welcome to create a new AlmaLinux account at {{ register_url }}.\n\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                "<p>Hi {{ full_name }},</p>\n"
                "<p>Your account deletion request submitted on {{ requested_at_utc }} has been approved, and your AlmaLinux account has now been deleted.</p>\n"
                "<p>If you did not request this change, contact the AlmaLinux accounts team immediately.</p>\n"
                "<p>If you would like to come back in the future, you are always welcome to <a href=\"{{ register_url }}\">create a new AlmaLinux account</a>.</p>\n"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0098_create_election_voting_reminder_and_concluded_email_templates"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            create_account_deletion_approved_email_template,
            migrations.RunPython.noop,
        ),
    ]