from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

import core.startup
from core.models import MembershipType
from core.startup import ensure_membership_type_groups_exist


class StartupMembershipGroupSyncTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        core.startup._membership_groups_synced = False

    def test_creates_missing_membership_type_groups(self) -> None:
        # The app seeds membership types with default group CNs; blank them out
        # so this test only exercises the explicit missing-group case.
        MembershipType.objects.filter(
            code__in=["individual", "mirror", "platinum", "gold", "silver", "ruby"],
        ).update(group_cn="")

        MembershipType.objects.update_or_create(
            code="individual_missing_group",
            defaults={
                "name": "Individual",
                "group_cn": "individual-members-missing",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        with (
            patch("core.startup.FreeIPAGroup.get", return_value=None),
            patch("core.startup.FreeIPAGroup.create") as create_mock,
        ):
            ensure_membership_type_groups_exist()

        create_mock.assert_called_once_with(cn="individual-members-missing", fas_group=False)

    def test_rejects_membership_type_groups_that_are_fas_groups(self) -> None:
        MembershipType.objects.filter(
            code__in=["individual", "mirror", "platinum", "gold", "silver", "ruby"],
        ).update(group_cn="")

        MembershipType.objects.update_or_create(
            code="individual_fas_group",
            defaults={
                "name": "Individual",
                "group_cn": "individual-members-fas",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        fas_group = type(
            "_Group",
            (),
            {"cn": "individual-members-fas", "fas_group": True},
        )()

        with patch("core.startup.FreeIPAGroup.get", return_value=fas_group):
            with self.assertRaisesMessage(ValueError, "FAS"):
                ensure_membership_type_groups_exist()
