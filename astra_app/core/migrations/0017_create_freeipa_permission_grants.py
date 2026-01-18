from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import migrations, models


def _seed_membership_committee_permissions(apps, schema_editor) -> None:
    FreeIPAPermissionGrant = apps.get_model("core", "FreeIPAPermissionGrant")

    perms = [
        "astra.add_membership",
        "astra.change_membership",
        "astra.delete_membership",
        "astra.view_membership",
    ]

    for perm in perms:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=perm,
            principal_type="group",
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_create_membership_committee_pending_requests_email_template"),
    ]

    operations = [
        migrations.CreateModel(
            name="FreeIPAPermissionGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("permission", models.CharField(db_index=True, max_length=150)),
                ("principal_type", models.CharField(choices=[("user", "User"), ("group", "Group")], db_index=True, max_length=10)),
                ("principal_name", models.CharField(db_index=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["principal_type", "principal_name"], name="idx_perm_grant_principal"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="freeipapermissiongrant",
            constraint=models.UniqueConstraint(
                fields=("permission", "principal_type", "principal_name"),
                name="uniq_freeipa_permission_grant",
            ),
        ),
        migrations.RunPython(_seed_membership_committee_permissions, migrations.RunPython.noop),
    ]
