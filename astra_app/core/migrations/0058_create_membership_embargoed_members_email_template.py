from __future__ import annotations

from django.db import migrations


def create_membership_embargoed_members_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        name="membership-committee-embargoed-members",
        defaults={
            "description": "Notify the membership committee about active members in embargoed countries",
            "subject": (
                "Active member{{ embargoed_count|pluralize }} in embargoed countr"
                "{{ embargoed_count|pluralize:'y,ies' }} ({{ embargoed_count }})"
            ),
            "html_content": (
                "<p>Hello Membership Committee,</p>\n"
                "<p>There {{ embargoed_count|pluralize:'is,are' }} <strong>{{ embargoed_count }}</strong> "
                "active member{{ embargoed_count|pluralize }} from{{ embargoed_count|pluralize:' an,' }} embargoed countr"
                "{{ embargoed_count|pluralize:'y,ies' }}.</p>\n"
                "<p>Member{{ embargoed_count|pluralize }}:</p>\n"
                "<ul>\n"
                "{% for member in embargoed_members %}"
                "<li>{{ member.full_name }} ({{ member.username }}) — {{ member.country_name }} ({{ member.country_code }})</li>"
                "{% endfor %}"
                "</ul>\n"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
            "content": (
                "Hello Membership Committee,\n\n"
                "There {{ embargoed_count|pluralize:'is,are' }} {{ embargoed_count }} active member"
                "{{ embargoed_count|pluralize }} from{{ embargoed_count|pluralize:' an,' }} embargoed countr{{ embargoed_count|pluralize:'y,ies' }}.\n\n"
                "Member{{ embargoed_count|pluralize }}:\n"
                "{% for member in embargoed_members %}"
                "- {{ member.full_name }} ({{ member.username }}) — {{ member.country_name }} ({{ member.country_code }})\n"
                "{% endfor %}\n"
                "-- The AlmaLinux Team\n"
            ),
        },
    )


def delete_membership_embargoed_members_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.filter(name="membership-committee-embargoed-members").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0057_organization_representative_unique"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            create_membership_embargoed_members_email_template,
            delete_membership_embargoed_members_email_template,
        ),
    ]
