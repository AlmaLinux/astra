from __future__ import annotations

import secrets

from django.db import migrations, models


def _populate_organization_claim_state(apps, schema_editor) -> None:
    Organization = apps.get_model("core", "Organization")

    for organization in Organization.objects.all().iterator():
        representative = str(organization.representative or "").strip()
        update_fields: list[str] = []

        if representative:
            if organization.status != "active":
                organization.status = "active"
                update_fields.append("status")
        else:
            if organization.status != "unclaimed":
                organization.status = "unclaimed"
                update_fields.append("status")
            if not str(organization.claim_secret or "").strip():
                organization.claim_secret = secrets.token_urlsafe(32)
                update_fields.append("claim_secret")

        if update_fields:
            organization.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0074_remove_organization_additional_information"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="status",
            field=models.CharField(
                choices=[("unclaimed", "Unclaimed"), ("active", "Active")],
                db_index=True,
                default="unclaimed",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="claim_secret",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="organization",
            name="representative",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(
            _populate_organization_claim_state,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="organization",
            name="core_organization_representative_not_empty",
        ),
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(status="unclaimed") & models.Q(representative=""))
                    | (models.Q(status="active") & ~models.Q(representative=""))
                ),
                name="core_organization_status_matches_representative",
            ),
        ),
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.CheckConstraint(
                condition=~(models.Q(status="unclaimed") & models.Q(claim_secret="")),
                name="core_organization_unclaimed_requires_claim_secret",
            ),
        ),
    ]
