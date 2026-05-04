import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class GroupsApiTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _make_group(
        self,
        cn: str,
        *,
        description: str = "",
        members: list[str] | None = None,
        sponsors: list[str] | None = None,
        sponsor_groups: list[str] | None = None,
    ) -> FreeIPAGroup:
        return FreeIPAGroup(
            cn,
            {
                "cn": [cn],
                "description": [description],
                "member_user": members or [],
                "membermanager_user": sponsors or [],
                "membermanager_group": sponsor_groups or [],
                "member_group": [],
                "objectclass": ["fasgroup"],
            },
        )

    def test_groups_api_returns_description_matches_excludes_non_fas_groups_and_preserves_payload_shape(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        alpha = self._make_group("alpha-team", description="Core Ops Team", members=["a", "b"])
        beta = self._make_group("beta-team", description="Beta Team", members=["c"])
        hidden = FreeIPAGroup(
            "ops-shadow",
            {
                "cn": ["ops-shadow"],
                "description": ["Core Ops Shadow"],
                "member_user": ["d"],
                "membermanager_user": [],
                "membermanager_group": [],
                "member_group": [],
                "objectclass": [],
            },
        )
        alpha.member_count_recursive = MagicMock(return_value=2)
        beta.member_count_recursive = MagicMock(return_value=1)
        hidden.member_count_recursive = MagicMock(return_value=7)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch("core.views_groups.FreeIPAGroup.all", return_value=[alpha, beta, hidden]):
                response = self.client.get(
                    reverse("api-groups"),
                    {"q": "OPS", "page": "1"},
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["q"], "OPS")
        self.assertEqual([item["cn"] for item in payload["items"]], ["alpha-team"])
        self.assertEqual(payload["items"][0]["member_count"], 2)
        self.assertNotIn("detail_url", payload["items"][0])
        self.assertEqual(payload["pagination"]["page"], 1)
        hidden.member_count_recursive.assert_not_called()

    def test_groups_api_sorts_items_by_lowercase_group_name(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        zulu = self._make_group("Zulu", description="Zulu Team", members=["z"])
        alpha = self._make_group("alpha", description="Alpha Team", members=["a"])
        beta = self._make_group("Beta", description="Beta Team", members=["b"])
        zulu.member_count_recursive = MagicMock(return_value=1)
        alpha.member_count_recursive = MagicMock(return_value=1)
        beta.member_count_recursive = MagicMock(return_value=1)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch("core.views_groups.FreeIPAGroup.all", return_value=[zulu, alpha, beta]):
                response = self.client.get(
                    reverse("api-groups"),
                    {"page": "1"},
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual([item["cn"] for item in payload["items"]], ["alpha", "Beta", "Zulu"])
        self.assertEqual(payload["pagination"]["page"], 1)

    def test_groups_page_renders_route_templates_for_vue_shell(self) -> None:
        self._login_as_freeipa_user("alice")
        response = self.client.get(reverse("groups"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-groups-api-url="/api/v1/groups"')
        self.assertContains(response, 'data-groups-detail-url-template="/group/__group_name__/"')

    def test_group_info_api_returns_group_info_without_paging(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        group = self._make_group(
            "infra-team",
            description="Infra Team",
            members=["alice", "bob"],
            sponsors=["alice"],
            sponsor_groups=["sponsor-subgroup"],
        )
        sponsor_subgroup = self._make_group("sponsor-subgroup", description="Subgroup")
        group.member_count_recursive = MagicMock(return_value=2)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch("core.views_groups.FreeIPAGroup.get", side_effect=lambda cn: group if cn == "infra-team" else sponsor_subgroup):
                with patch("core.views_groups.required_agreements_for_group", return_value=[]):
                    response = self.client.get(
                        reverse("api-group-detail-info", args=["infra-team"]),
                        HTTP_ACCEPT="application/json",
                    )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["group"]["cn"], "infra-team")
        self.assertTrue(payload["group"]["is_member"])
        self.assertTrue(payload["group"]["is_sponsor"])
        self.assertNotIn("members", payload["group"])
        self.assertNotIn("leaders", payload["group"])
        self.assertNotIn("sponsor_groups", payload["group"])
        self.assertEqual(payload["group"]["required_agreements"], [])
        self.assertEqual(payload["group"]["unsigned_usernames"], [])
        self.assertNotIn("edit_url", payload["group"])

    def test_group_info_api_accepts_mixed_case_group_path(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        group = self._make_group(
            "infra-team",
            description="Infra Team",
            members=["alice", "bob"],
            sponsors=["alice"],
        )
        group.member_count_recursive = MagicMock(return_value=2)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch(
                "core.views_groups.FreeIPAGroup.get",
                side_effect=lambda cn: group if cn == "infra-team" else None,
            ):
                with patch("core.views_groups.required_agreements_for_group", return_value=[]):
                    response = self.client.get(
                        reverse("api-group-detail-info", args=["INFRA-TEAM"]),
                        HTTP_ACCEPT="application/json",
                    )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["group"]["cn"], "infra-team")

    def test_group_info_api_omits_agreement_page_urls_from_required_agreements(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        group = self._make_group(
            "infra-team",
            description="Infra Team",
            members=["alice", "bob"],
            sponsors=["alice"],
        )
        group.member_count_recursive = MagicMock(return_value=2)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch("core.views_groups.FreeIPAGroup.get", return_value=group):
                with patch("core.views_groups.required_agreements_for_group", return_value=["almalinux-coc"]):
                    with patch(
                        "core.views_groups.FreeIPAFASAgreement.get",
                        return_value=SimpleNamespace(users=["alice"]),
                    ):
                        response = self.client.get(
                            reverse("api-group-detail-info", args=["infra-team"]),
                            HTTP_ACCEPT="application/json",
                        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["group"]["required_agreements"], [{"cn": "almalinux-coc", "signed": True}])
        self.assertEqual(payload["group"]["unsigned_usernames"], ["bob"])

    def test_group_detail_page_renders_route_templates_for_vue_shell(self) -> None:
        self._login_as_freeipa_user("alice")
        group = self._make_group("infra-team", description="Infra Team")

        with patch("core.views_groups.FreeIPAGroup.get", return_value=group):
            response = self.client.get(reverse("group-detail", args=["infra-team"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-group-detail-url-template="/group/__group_name__/"')
        self.assertContains(response, 'data-group-detail-edit-url-template="/group/__group_name__/edit/"')
        self.assertContains(response, 'data-group-detail-agreement-detail-url-template="/settings/?tab=agreements&amp;agreement=__agreement_cn__"')
        self.assertContains(response, 'data-group-detail-agreements-list-url="/settings/?tab=agreements"')

    def test_group_leaders_api_returns_paginated_mixed_leader_items(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        sponsor_user = FreeIPAUser("alice", {"uid": ["alice"], "cn": ["Alice Example"], "memberof_group": [], "c": ["US"]})

        group = self._make_group(
            "infra-team",
            description="Infra Team",
            members=["alice", "bob"],
            sponsors=["alice"],
            sponsor_groups=["sponsor-subgroup"],
        )
        sponsor_subgroup = self._make_group("sponsor-subgroup", description="Subgroup")

        def get_user(cn: str) -> FreeIPAUser | None:
            if cn == "alice":
                return sponsor_user
            return viewer

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user):
            with patch("core.views_groups.FreeIPAGroup.get", side_effect=lambda cn: group if cn == "infra-team" else sponsor_subgroup):
                with patch("core.views_groups.required_agreements_for_group", return_value=[]):
                    with patch("core.views_groups.resolve_avatar_urls_for_users", return_value=({"alice": "/avatars/alice.png"}, 1, 0)):
                        response = self.client.get(
                            reverse("api-group-detail-leaders", args=["infra-team"]),
                            {"page": "1"},
                            HTTP_ACCEPT="application/json",
                        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["leaders"]["pagination"]["page"], 1)
        self.assertEqual(payload["leaders"]["pagination"]["count"], 2)
        self.assertEqual(payload["leaders"]["items"][0]["kind"], "group")
        self.assertEqual(payload["leaders"]["items"][0]["cn"], "sponsor-subgroup")

        self.assertEqual(payload["leaders"]["items"][1]["kind"], "user")
        self.assertEqual(payload["leaders"]["items"][1]["username"], "alice")
        self.assertEqual(payload["leaders"]["items"][1]["full_name"], "Alice Example")
        self.assertEqual(payload["leaders"]["items"][1]["avatar_url"], "/avatars/alice.png")

    def test_group_members_api_returns_paginated_member_items(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        sponsor_user = FreeIPAUser("alice", {"uid": ["alice"], "cn": ["Alice Example"], "memberof_group": [], "c": ["US"]})
        member_user = FreeIPAUser("bob", {"uid": ["bob"], "cn": ["Bob Example"], "memberof_group": [], "c": ["US"]})

        group = self._make_group(
            "infra-team",
            description="Infra Team",
            members=["alice", "bob"],
            sponsors=["alice"],
            sponsor_groups=["sponsor-subgroup"],
        )
        sponsor_subgroup = self._make_group("sponsor-subgroup", description="Subgroup")
        group.member_count_recursive = MagicMock(return_value=2)

        def get_user(cn: str) -> FreeIPAUser | None:
            if cn == "infra-team":
                return None
            if cn == "sponsor-subgroup":
                return None
            if cn == "alice":
                return sponsor_user
            if cn == "bob":
                return member_user
            return viewer

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user):
            with patch("core.views_groups.FreeIPAGroup.get", side_effect=lambda cn: group if cn == "infra-team" else sponsor_subgroup):
                with patch("core.views_groups.required_agreements_for_group", return_value=[]):
                    with patch("core.views_groups.resolve_avatar_urls_for_users", return_value=({"alice": "/avatars/alice.png", "bob": "/avatars/bob.png"}, 2, 0)):
                        response = self.client.get(
                            reverse("api-group-detail-members", args=["infra-team"]),
                            {"q": "bo", "page": "1"},
                            HTTP_ACCEPT="application/json",
                        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["members"]["q"], "bo")
        self.assertEqual([item["username"] for item in payload["members"]["items"]], ["bob"])
        self.assertEqual(payload["members"]["items"][0]["full_name"], "Bob Example")
        self.assertEqual(payload["members"]["items"][0]["avatar_url"], "/avatars/bob.png")
        self.assertEqual(payload["members"]["pagination"]["page"], 1)

    def test_group_action_api_requires_team_lead_for_member_management(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        group = self._make_group("infra-team", members=["alice"], sponsors=[])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch("core.views_groups.FreeIPAGroup.get", return_value=group):
                with patch("core.views_groups.required_agreements_for_group", return_value=[]):
                    response = self.client.post(
                        reverse("api-group-action", args=["infra-team"]),
                        data=json.dumps({"action": "remove_member", "username": "bob"}),
                        content_type="application/json",
                        HTTP_ACCEPT="application/json",
                    )

        self.assertEqual(response.status_code, 403)
        payload = json.loads(response.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Only Team Leads can manage group members.")

    def test_group_edit_api_get_and_put_roundtrip(self) -> None:
        self._login_as_freeipa_user("alice")
        viewer = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        group = self._make_group("infra-team", description="Infra Team", sponsors=["alice"])
        group.fas_url = "https://example.com/group"
        group.fas_mailing_list = "infra@example.com"
        group.fas_discussion_url = "https://forums.example.com/infra"
        group.fas_irc_channels = ["#infra"]
        group.save = MagicMock()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            with patch("core.views_groups.FreeIPAGroup.get", return_value=group):
                get_response = self.client.get(reverse("api-group-edit", args=["infra-team"]), HTTP_ACCEPT="application/json")
                self.assertEqual(get_response.status_code, 200)
                get_payload = json.loads(get_response.content)
                self.assertEqual(get_payload["group"]["cn"], "infra-team")
                self.assertEqual(get_payload["group"]["fas_url"], "https://example.com/group")

                put_response = self.client.put(
                    reverse("api-group-edit", args=["infra-team"]),
                    data=json.dumps(
                        {
                            "description": "New Infra Team",
                            "fas_url": "https://example.com/new-group",
                            "fas_mailing_list": "new-infra@example.com",
                            "fas_discussion_url": "https://forums.example.com/new-infra",
                            "fas_irc_channels": "#infra\n#infra-dev",
                        }
                    ),
                    content_type="application/json",
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(put_response.status_code, 200)
        put_payload = json.loads(put_response.content)
        self.assertTrue(put_payload["ok"])
        self.assertEqual(put_payload["group"]["description"], "New Infra Team")
        self.assertEqual(put_payload["group"]["fas_irc_channels"], ["irc://#infra", "irc://#infra-dev"])
        group.save.assert_called_once()
