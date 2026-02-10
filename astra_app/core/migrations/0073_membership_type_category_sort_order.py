from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0072_drop_organization_membership_level_and_sponsorship"),
    ]

    operations = [
        migrations.AddField(
            model_name="membershiptypecategory",
            name="sort_order",
            field=models.IntegerField(
                default=0,
                help_text="Lower values appear first in membership type selections.",
            ),
        ),
    ]