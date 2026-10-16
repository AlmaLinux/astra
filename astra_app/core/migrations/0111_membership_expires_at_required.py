from django.db import migrations, models


def refuse_memberships_without_expiry(apps, schema_editor) -> None:
    # An expiry is a policy decision (term length, renewal date), so fail instead of guessing one.
    missing = list(
        apps.get_model("core", "Membership").objects.filter(expires_at__isnull=True).values_list("pk", flat=True)
    )
    if missing:
        raise RuntimeError(f"Set an expiry on these memberships without expires_at before migrating: {missing}")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0110_audit_log_organization_snapshot'),
    ]

    operations = [
        migrations.RunPython(refuse_memberships_without_expiry, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='membership',
            name='expires_at',
            field=models.DateTimeField(),
        ),
        migrations.AddConstraint(
            model_name='membershiplog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('action__in', ['approved', 'expiry_changed']), _negated=True), ('expires_at__isnull', False), _connector='OR'), name='chk_membershiplog_term_action_has_expiry'),
        ),
    ]
