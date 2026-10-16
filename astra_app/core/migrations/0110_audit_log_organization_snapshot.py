import django.db.models.deletion
from django.db import migrations, models

# The only organization event that used "actor"; every other one already says "actor_username".
_EVENT_TYPE = "organization_representative_changed"


def _rename_actor_key(apps, *, old: str, new: str) -> None:
    # Election entries keep "actor": their payloads are hashed and Rekor-timestamped.
    entries = apps.get_model("core", "AuditLogEntry").objects.filter(
        election__isnull=True,
        event_type=_EVENT_TYPE,
        rekor_log_id__isnull=True,
        payload__has_key=old,
    )
    for entry in entries:
        entry.payload[new] = entry.payload.pop(old)
        entry.save(update_fields=["payload"])


def actor_to_actor_username(apps, schema_editor) -> None:
    _rename_actor_key(apps, old="actor", new="actor_username")


def actor_username_to_actor(apps, schema_editor) -> None:
    _rename_actor_key(apps, old="actor_username", new="actor")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0109_drop_derived_columns'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlogentry',
            name='deleted_organization_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='auditlogentry',
            name='deleted_organization_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='auditlogentry',
            name='organization',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_log', to='core.organization'),
        ),
        migrations.RunPython(actor_to_actor_username, actor_username_to_actor),
        migrations.AddConstraint(
            model_name='auditlogentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('election__isnull', False), models.Q(('organization__isnull', False), _negated=True), models.Q(('deleted_organization_id__isnull', False), _negated=True)), models.Q(('organization__isnull', False), models.Q(('election__isnull', False), _negated=True), models.Q(('deleted_organization_id__isnull', False), _negated=True)), models.Q(('deleted_organization_id__isnull', False), models.Q(('election__isnull', False), _negated=True), models.Q(('organization__isnull', False), _negated=True)), _connector='OR'), name='chk_auditlogentry_exactly_one_target'),
        ),
    ]
