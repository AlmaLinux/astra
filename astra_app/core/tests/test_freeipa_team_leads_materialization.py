from unittest.mock import call, patch

from django.test import TestCase, override_settings

from core.freeipa.group import (
    FreeIPAGroup,
    resolve_materialized_team_leads_usernames,
    sync_materialized_team_leads_group,
)


class FreeIPATeamLeadsMaterializationTests(TestCase):
    def _group(
        self,
        cn: str,
        *,
        members: list[str] | None = None,
        member_groups: list[str] | None = None,
        sponsors: list[str] | None = None,
        sponsor_groups: list[str] | None = None,
        fas_group: bool = False,
    ) -> FreeIPAGroup:
        group_data: dict[str, object] = {
            "cn": [cn],
            "member_user": members or [],
            "member_group": member_groups or [],
            "membermanager_user": sponsors or [],
            "membermanager_group": sponsor_groups or [],
        }
        if fas_group:
            group_data["fasgroup"] = True
        return FreeIPAGroup(cn, group_data)

    @override_settings(
        MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN="custom_source",
        MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN="custom_destination",
    )
    def test_resolver_reads_only_direct_child_groups_and_ignores_group_managers(self) -> None:
        source_group = self._group(
            "custom_source",
            member_groups=["sig1", "sig2"],
        )
        sig1 = self._group(
            "sig1",
            member_groups=["sig1-subgroup"],
            sponsors=["jimbo"],
            sponsor_groups=["board_of_directors"],
        )
        sig1_subgroup = self._group("sig1-subgroup", sponsors=["recursive-user"])
        sig2 = self._group(
            "sig2",
            sponsors=["james"],
            sponsor_groups=["missing-manager-group"],
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn in {"board_of_directors", "missing-manager-group"}:
                raise AssertionError("group-valued member managers must not be traversed")
            return {
                "custom_source": source_group,
                "sig1": sig1,
                "sig1-subgroup": sig1_subgroup,
                "sig2": sig2,
            }.get(cn)

        with patch("core.freeipa.group.FreeIPAGroup.get", side_effect=_get_group):
            usernames = resolve_materialized_team_leads_usernames()

        self.assertEqual(usernames, {"jimbo", "james"})

    @override_settings(
        MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN="custom_source",
        MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN="custom_destination",
    )
    def test_sync_reconciles_exact_direct_users_and_removes_group_drift(self) -> None:
        source_group = self._group(
            "custom_source",
            member_groups=["sig1", "sig2"],
        )
        sig1 = self._group("sig1", sponsors=["jimbo"], sponsor_groups=["board_of_directors"])
        sig2 = self._group("sig2", sponsors=["james"])
        destination_group = self._group(
            "custom_destination",
            members=["legacy-user"],
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
            patch.object(FreeIPAGroup, "create", return_value=destination_group) as create_mock,
            patch.object(FreeIPAGroup, "add_member") as add_member_mock,
            patch.object(FreeIPAGroup, "remove_member") as remove_member_mock,
            patch.object(FreeIPAGroup, "remove_member_group") as remove_member_group_mock,
            patch.object(FreeIPAGroup, "remove_sponsor") as remove_sponsor_mock,
            patch.object(FreeIPAGroup, "remove_sponsor_group") as remove_sponsor_group_mock,
        ):
            sync_materialized_team_leads_group()

        create_mock.assert_not_called()
        add_member_mock.assert_has_calls([call("james"), call("jimbo")], any_order=True)
        remove_member_mock.assert_called_once_with("legacy-user")
        remove_member_group_mock.assert_called_once_with("legacy-nested")
        remove_sponsor_mock.assert_called_once_with("legacy-sponsor-user")
        remove_sponsor_group_mock.assert_called_once_with("legacy-manager-group")

    @override_settings(
        MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN="custom_source",
        MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN="custom_destination",
    )
    def test_sync_dry_run_reports_without_mutating(self) -> None:
        source_group = self._group(
            "custom_source",
            member_groups=["sig1", "sig2"],
        )
        sig1 = self._group("sig1", sponsors=["jimbo"])
        sig2 = self._group("sig2", sponsors=["james"])
        destination_group = self._group(
            "custom_destination",
            members=["legacy-user"],
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
            patch.object(FreeIPAGroup, "create", return_value=destination_group) as create_mock,
            patch.object(FreeIPAGroup, "add_member") as add_member_mock,
            patch.object(FreeIPAGroup, "remove_member") as remove_member_mock,
            patch.object(FreeIPAGroup, "remove_member_group") as remove_member_group_mock,
            patch.object(FreeIPAGroup, "remove_sponsor") as remove_sponsor_mock,
            patch.object(FreeIPAGroup, "remove_sponsor_group") as remove_sponsor_group_mock,
        ):
            report = sync_materialized_team_leads_group(dry_run=True)

        self.assertEqual(
            report,
            {
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
        )
        create_mock.assert_not_called()
        add_member_mock.assert_not_called()
        remove_member_mock.assert_not_called()
        remove_member_group_mock.assert_not_called()
        remove_sponsor_mock.assert_not_called()
        remove_sponsor_group_mock.assert_not_called()