from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0076_create_account_invite_org_claim_email_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountinvitation",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="account_invitations",
                to="core.organization",
            ),
        ),
    ]
