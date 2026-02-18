from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0078_organization_address_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="note",
            name="membership_request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="notes",
                to="core.membershiprequest",
            ),
        ),
    ]
