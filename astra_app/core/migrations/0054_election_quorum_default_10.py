from __future__ import annotations

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0053_update_election_credential_email_quorum_copy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="election",
            name="quorum",
            field=models.PositiveSmallIntegerField(
                default=10,
                help_text="Minimum turnout percentage required to conclude the election without extension.",
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
    ]
