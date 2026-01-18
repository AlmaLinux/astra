# Generated migration for user directory privacy restrictions

from django.conf import settings
from django.db import migrations

from core.permissions import ASTRA_VIEW_USER_DIRECTORY


def add_directory_permission_grants(apps, schema_editor):
    """Grant astra.view_user_directory to membership and election committees."""
    FreeIPAPermissionGrant = apps.get_model("core", "FreeIPAPermissionGrant")

    grants = [
        {
            "permission": ASTRA_VIEW_USER_DIRECTORY,
            "principal_type": "group",
            "principal_name": settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        },
        {
            "permission": ASTRA_VIEW_USER_DIRECTORY,
            "principal_type": "group",
            "principal_name": settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
        },
    ]

    for grant_data in grants:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=grant_data["permission"],
            principal_type=grant_data["principal_type"],
            principal_name=grant_data["principal_name"],
        )


def remove_directory_permission_grants(apps, schema_editor):
    """Remove astra.view_user_directory grants."""
    FreeIPAPermissionGrant = apps.get_model("core", "FreeIPAPermissionGrant")
    FreeIPAPermissionGrant.objects.filter(permission=ASTRA_VIEW_USER_DIRECTORY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0054_election_quorum_default_10"),
    ]

    operations = [
        migrations.RunPython(add_directory_permission_grants, remove_directory_permission_grants),
    ]
