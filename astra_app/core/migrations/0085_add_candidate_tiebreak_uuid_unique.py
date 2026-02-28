from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0084_repair_validate_membership_category_denorm"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="candidate",
            constraint=models.UniqueConstraint(
                fields=("election", "tiebreak_uuid"),
                name="uniq_candidate_election_tiebreak_uuid",
            ),
        ),
    ]
