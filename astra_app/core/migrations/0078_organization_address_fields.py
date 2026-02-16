from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0077_account_invitation_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="city",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="organization",
            name="country_code",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
        migrations.AddField(
            model_name="organization",
            name="postal_code",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="organization",
            name="state",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="organization",
            name="street",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
