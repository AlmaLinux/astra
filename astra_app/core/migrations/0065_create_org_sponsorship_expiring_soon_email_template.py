from __future__ import annotations

from django.db import migrations


def create_org_sponsorship_expiring_soon_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        name="organization-sponsorship-expiring-soon",
        defaults={
            "description": "Organization sponsorship expiration warning",
            "subject": "Your AlmaLinux sponsorship expires in {{ days }} day{{ days|pluralize }}",
            "content": (
                "== AlmaLinux Account Services ==\n\n"
                "Hello {{ full_name|default:username }},\n\n"
                "Thank you for supporting the AlmaLinux OS Foundation through your organization sponsorship. "
                "Sponsorships are the lifeblood of the Foundation, and your partnership helps us sustain a stable, "
                "community-driven enterprise Linux ecosystem.\n\n"
                "This is a friendly reminder that the sponsorship for {{ organization_name }} is expiring in "
                "{{ days }} day{{ days|pluralize }} ({{ expires_at }}).\n\n"
                "Organization: {{ organization_name }} (ID: {{ organization_id }})\n"
                "Sponsorship level: {{ membership_type }}\n"
                "Expiration: {{ expires_at }}\n\n"
                "---\n"
                "Extend your sponsorship\n\n"
                "To keep your sponsorship active, please extend it here:\n\n"
                "  {{ extend_url }}\n\n"
                "If you have already extended, you can safely ignore this email. "
                "This message is copied to the membership committee so they can assist if needed.\n\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                "<p><strong>AlmaLinux Account Services</strong></p>"
                "<p>Hello {{ full_name|default:username }},</p>"
                "<p>Thank you for supporting the AlmaLinux OS Foundation through your organization sponsorship. "
                "Sponsorships are the lifeblood of the Foundation, and your partnership helps us sustain a stable, "
                "community-driven enterprise Linux ecosystem.</p>"
                "<p>This is a friendly reminder that the sponsorship for <strong>{{ organization_name }}</strong> will expire in "
                "<strong>{{ days }} day{{ days|pluralize }}</strong> ({{ expires_at }}).</p>"
                "<p><strong>Organization:</strong> {{ organization_name }} (ID: {{ organization_id }})</p>"
                "<p><strong>Sponsorship level:</strong> {{ membership_type }}</p>"
                "<p><strong>Expiration:</strong> {{ expires_at }}</p>"
                "<hr>"
                "<h3>Extend your sponsorship</h3>"
                "<p>To keep your sponsorship active, please extend it using the link below:</p>"
                "<p><a href=\"{{ extend_url }}\">Extend sponsorship</a></p>"
                "<p>If you have already extended, you can safely ignore this email. "
                "This message is copied to the membership committee so they can assist if needed.</p>"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
        },
    )


def delete_org_sponsorship_expiring_soon_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.filter(name="organization-sponsorship-expiring-soon").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0064_create_freeipa_membership_reconcile_email_template"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            create_org_sponsorship_expiring_soon_email_template,
            delete_org_sponsorship_expiring_soon_email_template,
        ),
    ]
