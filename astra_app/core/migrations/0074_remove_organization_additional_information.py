from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0073_membership_type_category_sort_order"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="organization",
            name="additional_information",
        ),
    ]
