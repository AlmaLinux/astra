from __future__ import annotations

from django.db import migrations


def create_freeipa_membership_reconcile_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        name="freeipa-membership-reconcile-alert",
        defaults={
            "description": "Alert FreeIPA admins about membership reconciliation drift",
            "subject": (
                "FreeIPA membership reconciliation {{ mode|upper }}: "
                "{{ total_missing }} missing, {{ total_extra }} extra, {{ total_errors }} errors"
            ),
            "html_content": (
                "<p>Hello FreeIPA Admins,</p>\n"
                "<p>FreeIPA membership reconciliation run ({{ mode }}) at {{ run_at }}.</p>\n"
                "<p>Summary: missing {{ total_missing }}, extra {{ total_extra }}, errors {{ total_errors }}.</p>\n"
                "<ul>\n"
                "{% for group in groups %}"
                "<li><strong>{{ group.group_cn }}</strong>: missing {{ group.missing_count }}, "
                "extra {{ group.extra_count }}, errors {{ group.errors|length }}"
                "{% if group.missing_sample %}<br>Missing sample: {{ group.missing_sample|join:", " }}{% endif %}"
                "{% if group.extra_sample %}<br>Extra sample: {{ group.extra_sample|join:", " }}{% endif %}"
                "</li>"
                "{% endfor %}"
                "</ul>\n"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
            "content": (
                "Hello FreeIPA Admins,\n\n"
                "FreeIPA membership reconciliation run ({{ mode }}) at {{ run_at }}.\n"
                "Summary: missing {{ total_missing }}, extra {{ total_extra }}, errors {{ total_errors }}.\n\n"
                "{% for group in groups %}"
                "- {{ group.group_cn }}: missing {{ group.missing_count }}, extra {{ group.extra_count }}, "
                "errors {{ group.errors|length }}\n"
                "{% if group.missing_sample %}  Missing sample: {{ group.missing_sample|join:", " }}\n{% endif %}"
                "{% if group.extra_sample %}  Extra sample: {{ group.extra_sample|join:", " }}\n{% endif %}"
                "{% endfor %}\n"
                "-- The AlmaLinux Team\n"
            ),
        },
    )


def delete_freeipa_membership_reconcile_email_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.filter(name="freeipa-membership-reconcile-alert").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0063_update_membership_email_contact_context"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            create_freeipa_membership_reconcile_email_template,
            delete_freeipa_membership_reconcile_email_template,
        ),
    ]
