"""Drop legacy Organization.membership_level FK and OrganizationSponsorship table.

All sponsorship state is now stored in Membership rows with
target_organization set and category FK pointing at the appropriate
MembershipTypeCategory.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0071_drop_legacy_boolean_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="organization",
            name="membership_level",
        ),
        migrations.DeleteModel(
            name="OrganizationSponsorship",
        ),
    ]
