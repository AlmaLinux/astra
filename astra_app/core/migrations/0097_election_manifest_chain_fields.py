from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0096_update_election_vote_receipt_template_ballot_verification_copy"),
    ]

    operations = [
        migrations.AddField(
            model_name="election",
            name="chain_version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="election",
            name="config_manifest_version",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="election",
            name="config_manifest",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="election",
            name="config_manifest_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="election",
            name="chain_anchor_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]