from __future__ import annotations

import logging

from django.conf import settings
from django.db import migrations

from core.permissions import ASTRA_ADD_ELECTION

logger = logging.getLogger(__name__)


_MEMBERSHIP_TYPE_GROUP_CNS: dict[str, str] = {
    "individual": "individual-members",
    "mirror": "mirror-members",
    "silver": "silver-sponsors",
    "ruby": "ruby-sponsors",
    "gold": "gold-sponsors",
    "platinum": "platinum-sponsors",
}


_NON_FASGROUP_CNS: tuple[str, ...] = (
    "individual-members",
    "mirror-members",
    "silver-sponsors",
    "ruby-sponsors",
    "gold-sponsors",
    "platinum-sponsors",
)


_FASGROUP_CNS: tuple[str, ...] = (
    settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
    settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
)


def _seed_membership_type_group_cns(apps, schema_editor) -> None:
    MembershipType = apps.get_model("core", "MembershipType")

    for membership_type_code, group_cn in _MEMBERSHIP_TYPE_GROUP_CNS.items():
        MembershipType.objects.filter(code=membership_type_code).update(group_cn=group_cn)


def _seed_election_committee_permission(apps, schema_editor) -> None:
    FreeIPAPermissionGrant = apps.get_model("core", "FreeIPAPermissionGrant")

    FreeIPAPermissionGrant.objects.get_or_create(
        permission=ASTRA_ADD_ELECTION,
        principal_type="group",
        principal_name=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
    )


def _ensure_freeipa_groups_exist(apps, schema_editor) -> None:
    # Import lazily so migrations can be imported without pulling in the FreeIPA
    # client unless the migration actually executes.
    from core.backends import FreeIPAGroup  # noqa: PLC0415

    for cn in _NON_FASGROUP_CNS:
        group = FreeIPAGroup.get(cn)
        if group is None:
            logger.info("Creating missing FreeIPA group cn=%r (fas_group=False)", cn)
            FreeIPAGroup.create(cn=cn, fas_group=False)
            continue

        if bool(group.fas_group):
            raise ValueError(f"Group {cn!r} must not be a fasGroup")

    for cn in _FASGROUP_CNS:
        group = FreeIPAGroup.get(cn)
        if group is None:
            logger.info("Creating missing FreeIPA group cn=%r (fas_group=True)", cn)
            FreeIPAGroup.create(cn=cn, fas_group=True)
            continue

        if not bool(group.fas_group):
            raise ValueError(f"Group {cn!r} must be a fasGroup")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0050_reset_agreements_to_almalinux_coc"),
    ]

    operations = [
        migrations.RunPython(_ensure_freeipa_groups_exist, migrations.RunPython.noop),
        migrations.RunPython(_seed_membership_type_group_cns, migrations.RunPython.noop),
        migrations.RunPython(_seed_election_committee_permission, migrations.RunPython.noop),
    ]
