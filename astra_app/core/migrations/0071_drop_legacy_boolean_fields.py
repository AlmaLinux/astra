"""Drop legacy isIndividual/isOrganization boolean fields from MembershipType.

These are replaced by the MembershipTypeCategory FK (category) with its
is_individual / is_organization booleans.  All code references have been
updated to use category__is_individual / category__is_organization.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0070_category_not_null_drop_legacy_booleans"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="membershiptype",
            name="isIndividual",
        ),
        migrations.RemoveField(
            model_name="membershiptype",
            name="isOrganization",
        ),
    ]
