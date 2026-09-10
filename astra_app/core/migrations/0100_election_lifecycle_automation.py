from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0099_create_account_deletion_approved_email_template")]

    operations = [
        migrations.AddField(
            model_name="election",
            name="auto_end_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="election",
            name="auto_start_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="ElectionAutoEndDeferral",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scheduled_for", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("election", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="auto_end_deferrals", to="core.election")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("election", "scheduled_for"), name="uniq_election_auto_end_deferral_deadline")],
            },
        ),
    ]