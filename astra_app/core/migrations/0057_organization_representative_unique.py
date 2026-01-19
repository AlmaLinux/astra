from __future__ import annotations

from collections import Counter

from django.db import migrations, models


def _preflight_validate_unique_representatives(apps, schema_editor) -> None:
    Organization = apps.get_model("core", "Organization")

    representatives = list(
        Organization.objects.values_list("representative", flat=True)
    )

    blank_count = sum(1 for r in representatives if str(r or "").strip() == "")
    if blank_count:
        raise RuntimeError(
            "Cannot enforce unique organization representatives: found organizations with blank representative. "
            "Assign a representative to every organization, then re-run migrations. "
            "Hint (SQL): SELECT id, name FROM core_organization WHERE representative = '';"
        )

    normalized = [str(r).strip() for r in representatives if str(r or "").strip()]
    duplicates = [rep for rep, count in Counter(normalized).items() if count > 1]
    if duplicates:
        shown = ", ".join(sorted(duplicates)[:10])
        extra = "" if len(duplicates) <= 10 else f" (and {len(duplicates) - 10} more)"
        raise RuntimeError(
            "Cannot enforce unique organization representatives: some usernames are representative for multiple organizations. "
            f"Resolve duplicates and re-run migrations. Duplicate representatives: {shown}{extra}. "
            "Hint (SQL): SELECT representative, COUNT(*) FROM core_organization GROUP BY representative HAVING COUNT(*) > 1;"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0056_update_election_vote_receipt_template_add_weight"),
    ]

    operations = [
        migrations.RunPython(
            _preflight_validate_unique_representatives,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="organization",
            name="representative",
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.CheckConstraint(
                condition=~models.Q(representative=""),
                name="core_organization_representative_not_empty",
            ),
        ),
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.UniqueConstraint(
                fields=("representative",),
                condition=~models.Q(representative=""),
                name="core_organization_unique_representative",
            ),
        ),
    ]
