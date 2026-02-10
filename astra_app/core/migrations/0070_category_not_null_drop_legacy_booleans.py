"""Make MembershipType.category and Membership.category NOT NULL now that the
data migration has populated them.

Legacy isIndividual/isOrganization fields are dropped separately in migration
0071, AFTER all code references have been updated.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0069_populate_membership_categories"),
    ]

    operations = [
        migrations.AlterField(
            model_name="membershiptype",
            name="category",
            field=models.ForeignKey(
                help_text="Broad category: individual, mirror, or sponsorship.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="membership_types",
                to="core.membershiptypecategory",
            ),
        ),
        migrations.AlterField(
            model_name="membership",
            name="category",
            field=models.ForeignKey(
                help_text="Denormalized from membership_type.category for UniqueConstraint.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="core.membershiptypecategory",
            ),
        ),
    ]
