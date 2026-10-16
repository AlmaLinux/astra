from django.db import migrations, models
from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Cast

# (model, prefix of the org FK / legacy snapshot columns)
_SNAPSHOT_MODELS = (("MembershipRequest", "requested"), ("MembershipLog", "target"))


def snapshot_deleted_organizations(apps, schema_editor) -> None:
    """Only rows whose org is gone keep a snapshot; the legacy code is always the old org pk."""
    for model_name, prefix in _SNAPSHOT_MODELS:
        apps.get_model("core", model_name).objects.filter(
            **{f"{prefix}_organization__isnull": True},
        ).exclude(**{f"{prefix}_organization_code": ""}).update(
            deleted_organization_id=Cast(f"{prefix}_organization_code", models.BigIntegerField()),
            deleted_organization_name=F(f"{prefix}_organization_name"),
        )


def restore_organization_codes(apps, schema_editor) -> None:
    organization_model = apps.get_model("core", "Organization")
    for model_name, prefix in (*_SNAPSHOT_MODELS, ("Membership", "target")):
        model = apps.get_model("core", model_name)
        model.objects.filter(**{f"{prefix}_organization__isnull": False}).update(
            **{
                f"{prefix}_organization_code": Cast(f"{prefix}_organization_id", models.CharField()),
                f"{prefix}_organization_name": Subquery(
                    organization_model.objects.filter(pk=OuterRef(f"{prefix}_organization_id")).values("name")[:1]
                ),
            }
        )
    for model_name, prefix in _SNAPSHOT_MODELS:
        apps.get_model("core", model_name).objects.filter(deleted_organization_id__isnull=False).update(
            **{
                f"{prefix}_organization_code": Cast("deleted_organization_id", models.CharField()),
                f"{prefix}_organization_name": F("deleted_organization_name"),
            }
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0107_backfill_lifecycle_log_requests'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='membership',
            name='chk_membership_exactly_one_target',
        ),
        migrations.RemoveConstraint(
            model_name='membershiplog',
            name='chk_membershiplog_exactly_one_target',
        ),
        migrations.RemoveConstraint(
            model_name='membershiprequest',
            name='chk_membershiprequest_exactly_one_target',
        ),
        migrations.RemoveIndex(
            model_name='membershiplog',
            name='ml_org_code_at',
        ),
        migrations.RemoveIndex(
            model_name='membershiplog',
            name='ml_org_code_act_at',
        ),
        migrations.RemoveIndex(
            model_name='membershiprequest',
            name='mr_org_code_status',
        ),
        migrations.AddField(
            model_name='membershiplog',
            name='deleted_organization_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='membershiplog',
            name='deleted_organization_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='membershiprequest',
            name='deleted_organization_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='membershiprequest',
            name='deleted_organization_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddIndex(
            model_name='membershiplog',
            index=models.Index(fields=['deleted_organization_id', 'created_at'], name='ml_deleted_org_at'),
        ),
        migrations.RunPython(snapshot_deleted_organizations, restore_organization_codes),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('target_username', ''), _negated=True), ('target_organization__isnull', True)), models.Q(('target_username', ''), ('target_organization__isnull', False)), _connector='OR'), name='chk_membership_exactly_one_target'),
        ),
        migrations.AddConstraint(
            model_name='membershiplog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('target_username', ''), _negated=True), ('target_organization__isnull', True), ('deleted_organization_id__isnull', True)), models.Q(('target_username', ''), models.Q(('target_organization__isnull', True), _negated=True), ('deleted_organization_id__isnull', True)), models.Q(('target_username', ''), ('target_organization__isnull', True), models.Q(('deleted_organization_id__isnull', True), _negated=True)), _connector='OR'), name='chk_membershiplog_exactly_one_target'),
        ),
        migrations.AddConstraint(
            model_name='membershiprequest',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('requested_username', ''), _negated=True), ('requested_organization__isnull', True), ('deleted_organization_id__isnull', True)), models.Q(('requested_username', ''), models.Q(('requested_organization__isnull', True), _negated=True), ('deleted_organization_id__isnull', True)), models.Q(('requested_username', ''), ('requested_organization__isnull', True), models.Q(('deleted_organization_id__isnull', True), _negated=True)), _connector='OR'), name='chk_membershiprequest_exactly_one_target'),
        ),
        migrations.RemoveField(
            model_name='membership',
            name='target_organization_code',
        ),
        migrations.RemoveField(
            model_name='membership',
            name='target_organization_name',
        ),
        migrations.RemoveField(
            model_name='membershiplog',
            name='target_organization_code',
        ),
        migrations.RemoveField(
            model_name='membershiplog',
            name='target_organization_name',
        ),
        migrations.RemoveField(
            model_name='membershiprequest',
            name='requested_organization_code',
        ),
        migrations.RemoveField(
            model_name='membershiprequest',
            name='requested_organization_name',
        ),
    ]
