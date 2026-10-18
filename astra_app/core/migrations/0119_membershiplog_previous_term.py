from django.db import migrations, models
from django.db.models import Q

# Actions that set or end a membership term; the one before a change is what it changed from.
_TERM_ACTIONS = ("approved", "expiry_changed", "dues_changed", "terminated")


def backfill_previous_term(apps, schema_editor) -> None:
    """Fill what each existing expiry/dues change changed from, replaying the target's term history."""
    MembershipLog = apps.get_model("core", "MembershipLog")
    changes = MembershipLog.objects.filter(action__in=["expiry_changed", "dues_changed"]).select_related("membership_type")
    for change in changes:
        if change.target_username:
            target = {"target_username": change.target_username}
        elif change.target_organization_id is not None:
            target = {"target_organization_id": change.target_organization_id}
        else:
            target = {"deleted_organization_id": change.deleted_organization_id}
        previous = (
            MembershipLog.objects.filter(
                **target,
                membership_type__category_id=change.membership_type.category_id,
                action__in=_TERM_ACTIONS,
            )
            .filter(Q(created_at__lt=change.created_at) | Q(created_at=change.created_at, pk__lt=change.pk))
            .order_by("-created_at", "-pk")
            .first()
        )
        # After a termination there was no term to change from.
        if previous is None or previous.action == "terminated":
            continue
        if change.action == "expiry_changed":
            change.previous_expires_at = previous.expires_at
            change.save(update_fields=["previous_expires_at"])
        elif previous.membership_type_id == change.membership_type_id:
            change.previous_dues_usd = previous.dues_usd
            change.save(update_fields=["previous_dues_usd"])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0118_auditlogentry_election_only'),
    ]

    operations = [
        migrations.AddField(
            model_name='membershiplog',
            name='previous_dues_usd',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='membershiplog',
            name='previous_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_previous_term, migrations.RunPython.noop),
    ]
