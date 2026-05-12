import io
from unittest.mock import call, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.freeipa.group import FreeIPAGroup


class FreeIPATeamLeadsSyncCommandTests(TestCase):
    def _group(
        self,
        cn: str,
        *,
        members: list[str] | None = None,
        member_groups: list[str] | None = None,
        sponsors: list[str] | None = None,
        sponsor_groups: list[str] | None = None,
    ) -> FreeIPAGroup:
        return FreeIPAGroup(
            cn,
            {
                "cn": [cn],
                "member_user": members or [],
                "member_group": member_groups or [],
                "membermanager_user": sponsors or [],
                "membermanager_group": sponsor_groups or [],
            },
        )

    @override_settings(
        MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN="custom_source",
        MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN="custom_destination",
    )
    def test_command_creates_non_fas_destination_from_fixed_source(self) -> None:
        stdout = io.StringIO()
        source_group = self._group(
            "custom_source",
            member_groups=["sig1", "sig2"],
        )
        sig1 = self._group("sig1", sponsors=["jimbo"], sponsor_groups=["board_of_directors"])
        sig2 = self._group("sig2", sponsors=["james"])
        destination_group = self._group("custom_destination")

        def _get_group(cn: str) -> FreeIPAGroup | None:
            return {
                "custom_source": source_group,
                "sig1": sig1,
                "sig2": sig2,
                "custom_destination": None,
            }.get(cn)

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.freeipa.group.FreeIPAGroup.create", return_value=destination_group) as create_mock,
            patch.object(FreeIPAGroup, "add_member") as add_member_mock,
            patch.object(FreeIPAGroup, "remove_member") as remove_member_mock,
            patch.object(FreeIPAGroup, "remove_member_group") as remove_member_group_mock,
            patch.object(FreeIPAGroup, "remove_sponsor_group") as remove_sponsor_group_mock,
        ):
            call_command("freeipa_team_leads_sync", stdout=stdout)

        create_mock.assert_called_once_with("custom_destination", fas_group=False)
        add_member_mock.assert_has_calls([call("james"), call("jimbo")], any_order=True)
        remove_member_mock.assert_not_called()
        remove_member_group_mock.assert_not_called()
        remove_sponsor_group_mock.assert_not_called()
        self.assertIn("Created destination group custom_destination.", stdout.getvalue())
        self.assertIn("Adding 2 user(s): james, jimbo", stdout.getvalue())

    @override_settings(
        MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN="custom_source",
        MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN="custom_destination",
    )
    def test_command_reuses_destination_and_repairs_drift(self) -> None:
        stdout = io.StringIO()
        source_group = self._group(
            "custom_source",
            member_groups=["sig1", "sig2"],
        )
        sig1 = self._group("sig1", sponsors=["jimbo"], sponsor_groups=["board_of_directors"])
        sig2 = self._group("sig2", sponsors=["james"])
        destination_group = self._group(
            "custom_destination",
            members=["extra-user"],
            member_groups=["legacy-nested"],
            sponsors=["legacy-sponsor-user"],
            sponsor_groups=["legacy-manager-group"],
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            return {
                "custom_source": source_group,
                "sig1": sig1,
                "sig2": sig2,
                "custom_destination": destination_group,
            }.get(cn)

        with (
            patch("core.freeipa.group.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.freeipa.group.FreeIPAGroup.create") as create_mock,
            patch.object(FreeIPAGroup, "add_member") as add_member_mock,
            patch.object(FreeIPAGroup, "remove_member") as remove_member_mock,
            patch.object(FreeIPAGroup, "remove_member_group") as remove_member_group_mock,
            patch.object(FreeIPAGroup, "remove_sponsor") as remove_sponsor_mock,
            patch.object(FreeIPAGroup, "remove_sponsor_group") as remove_sponsor_group_mock,
        ):
            call_command("freeipa_team_leads_sync", stdout=stdout)

        create_mock.assert_not_called()
        add_member_mock.assert_has_calls([call("james"), call("jimbo")], any_order=True)
        remove_member_mock.assert_called_once_with("extra-user")
        remove_member_group_mock.assert_called_once_with("legacy-nested")
        remove_sponsor_mock.assert_called_once_with("legacy-sponsor-user")
        remove_sponsor_group_mock.assert_called_once_with("legacy-manager-group")
        self.assertIn("Removing 1 direct user(s): extra-user", stdout.getvalue())
        self.assertIn("Removing 1 nested group(s): legacy-nested", stdout.getvalue())
        self.assertIn("Removing 1 sponsor user(s): legacy-sponsor-user", stdout.getvalue())
        self.assertIn("Removing 1 sponsor group(s): legacy-manager-group", stdout.getvalue())
        self.assertIn("Adding 2 user(s): james, jimbo", stdout.getvalue())

    @override_settings(
        MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN="custom_source",
        MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN="custom_destination",
    )
    def test_command_dry_run_reports_without_mutating(self) -> None:
        stdout = io.StringIO()

        with patch(
            "core.management.commands.freeipa_team_leads_sync.sync_materialized_team_leads_group",
            return_value={
                "dry_run": True,
                "source_group_cn": "custom_source",
                "destination_group_cn": "custom_destination",
                "create_destination": False,
                "add_members": ["james", "jimbo"],
                "remove_members": ["legacy-user"],
                "remove_member_groups": ["legacy-nested"],
                "remove_sponsors": ["legacy-sponsor-user"],
                "remove_sponsor_groups": ["legacy-manager-group"],
            },
        ) as sync_mock:
            call_command("freeipa_team_leads_sync", "--dry-run", stdout=stdout)

        sync_mock.assert_called_once_with(dry_run=True)
        self.assertIn("[dry-run] Team-leads sync custom_source -> custom_destination", stdout.getvalue())
        self.assertIn("Adding 2 user(s): james, jimbo", stdout.getvalue())
        self.assertIn("Removing 1 direct user(s): legacy-user", stdout.getvalue())
        self.assertIn("Removing 1 nested group(s): legacy-nested", stdout.getvalue())
        self.assertIn("Removing 1 sponsor user(s): legacy-sponsor-user", stdout.getvalue())
        self.assertIn("Removing 1 sponsor group(s): legacy-manager-group", stdout.getvalue())