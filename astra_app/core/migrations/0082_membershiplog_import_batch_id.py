from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_account_invitation_accepted_username"),
    ]

    operations = [
        migrations.AddField(
            model_name="membershiplog",
            name="import_batch_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
