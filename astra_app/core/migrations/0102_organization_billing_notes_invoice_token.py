import secrets

from django.db import migrations, models

import core.models


def backfill_invoice_tokens(apps, schema_editor) -> None:
    Organization = apps.get_model("core", "Organization")
    for organization in Organization.objects.filter(invoice_token__isnull=True).only("pk"):
        organization.invoice_token = secrets.token_urlsafe(24)
        organization.save(update_fields=["invoice_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0101_membershiptype_annual_dues_usd"),
    ]

    # The token column is added nullable, backfilled, then made unique: a callable
    # default on AddField would give every existing row the same token.
    operations = [
        migrations.AddField(
            model_name="organization",
            name="billing_notes",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Printed on sponsorship invoices, e.g. a purchase order (PO) number.",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="invoice_token",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(backfill_invoice_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="organization",
            name="invoice_token",
            field=models.CharField(
                default=core.models.generate_invoice_token,
                editable=False,
                max_length=64,
                unique=True,
            ),
        ),
    ]
