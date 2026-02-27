from django.db import migrations
from django.db.models import Count


def _effective_category_duplicates(Membership):
    return list(
        Membership.objects.filter(target_organization__isnull=False)
        .values("target_organization_id", "membership_type__category_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("target_organization_id", "membership_type__category_id")[:20]
    )


def validate_no_effective_category_duplicates(apps, schema_editor):
    Membership = apps.get_model("core", "Membership")

    duplicates = _effective_category_duplicates(Membership)
    if duplicates:
        raise RuntimeError(
            "Cannot drop Membership.category denormalization because duplicate org memberships "
            f"exist by effective category (sample): {duplicates}"
        )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0083_add_membership_log_action_reopened"),
    ]

    operations = [
        migrations.RunPython(validate_no_effective_category_duplicates, noop_reverse),
        migrations.RemoveConstraint(
            model_name="membership",
            name="uniq_membership_org_category",
        ),
        migrations.RemoveField(
            model_name="membership",
            name="category",
        ),
    ]
