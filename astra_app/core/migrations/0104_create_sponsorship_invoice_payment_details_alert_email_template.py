from django.db import migrations

TEMPLATE_NAME = "sponsorship-invoice-payment-details-alert"


def create_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.update_or_create(
        name=TEMPLATE_NAME,
        defaults={
            "description": "Alert FreeIPA admins that the sponsorship invoice payment details file looks wrong or changed",
            "subject": "Check the sponsorship invoice payment details",
            "content": (
                "Hello FreeIPA Admins,\n\n"
                "The daily check of the bank details printed on sponsorship invoices ({{ path }}) found:\n\n"
                "{% for problem in problems %}- {{ problem }}\n{% endfor %}\n"
                "Sponsors pay to whatever this file says. If nobody changed it on purpose, "
                "treat it as a possible tampering attempt and verify it before more invoices go out.\n\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                "<p>Hello FreeIPA Admins,</p>\n"
                "<p>The daily check of the bank details printed on sponsorship invoices "
                "(<code>{{ path }}</code>) found:</p>\n"
                "<ul>{% for problem in problems %}<li>{{ problem }}</li>{% endfor %}</ul>\n"
                "<p>Sponsors pay to whatever this file says. If nobody changed it on purpose, "
                "treat it as a possible tampering attempt and verify it before more invoices go out.</p>\n"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
        },
    )


def delete_template(apps, schema_editor) -> None:
    apps.get_model("post_office", "EmailTemplate").objects.filter(name=TEMPLATE_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0103_sponsorship_invoice_email_blocks"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(create_template, delete_template),
    ]
