from __future__ import annotations

from django.db import migrations


def create_org_sponsorship_expired_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.update_or_create(
        name="organization-sponsorship-expired",
        defaults={
            "description": "Organization sponsorship expired",
            "subject": "Your AlmaLinux sponsorship has expired",
            "content": (
                "== AlmaLinux Account Services ==\n\n"
                "Hello {{ full_name|default:username }},\n\n"
                "Thank you for supporting the AlmaLinux OS Foundation through your organization sponsorship. "
                "Sponsorships are the lifeblood of the Foundation, and your partnership helps us sustain a stable, "
                "community-driven enterprise Linux ecosystem.\n\n"
                "Our records show that the sponsorship for {{ organization_name }} has expired ({{ expires_at }}).\n\n"
                "Organization: {{ organization_name }} (ID: {{ organization_id }})\n"
                "Sponsorship level: {{ membership_type }}\n"
                "Expired at: {{ expires_at }}\n\n"
                "---\n"
                "Renew your sponsorship\n\n"
                "To renew or manage your sponsorship, please use the link below:\n\n"
                "  {{ extend_url }}\n\n"
                "If you have already renewed, you can safely ignore this email. "
                "This message is copied to the membership committee so they can assist if needed.\n\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                "<p><strong>AlmaLinux Account Services</strong></p>"
                "<p>Hello {{ full_name|default:username }},</p>"
                "<p>Thank you for supporting the AlmaLinux OS Foundation through your organization sponsorship. "
                "Sponsorships are the lifeblood of the Foundation, and your partnership helps us sustain a stable, "
                "community-driven enterprise Linux ecosystem.</p>"
                "<p>Our records show that the sponsorship for <strong>{{ organization_name }}</strong> has expired "
                "({{ expires_at }}).</p>"
                "<p><strong>Organization:</strong> {{ organization_name }} (ID: {{ organization_id }})</p>"
                "<p><strong>Sponsorship level:</strong> {{ membership_type }}</p>"
                "<p><strong>Expired at:</strong> {{ expires_at }}</p>"
                "<hr>"
                "<h3>Renew your sponsorship</h3>"
                "<p>To renew or manage your sponsorship, please use the link below:</p>"
                "<p><a href=\"{{ extend_url }}\">Manage sponsorship</a></p>"
                "<p>If you have already renewed, you can safely ignore this email. "
                "This message is copied to the membership committee so they can assist if needed.</p>"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
        },
    )


def delete_org_sponsorship_expired_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.filter(name="organization-sponsorship-expired").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0065_create_org_sponsorship_expiring_soon_email_template"),
    ]

    operations = [
        migrations.RunPython(
            create_org_sponsorship_expired_email_template,
            delete_org_sponsorship_expired_email_template,
        ),
    ]
