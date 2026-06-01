import io
import json

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.group import FreeIPAGroup


class GroupsResetCommandTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("groups_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_deterministic_visible_inventory_and_payload_idempotently(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()

        call_command("groups_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("groups_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload, second_payload)
        self.assertEqual(first_payload["scenario"], "groups")
        self.assertEqual(first_payload["status"], "reset")
        self.assertEqual(set(first_payload["actors"].keys()), {"viewer", "sponsor"})
        self.assertEqual(
            set(first_payload["users"].keys()),
            {
                "detail_direct_member",
                "detail_child_member",
                "detail_grandchild_member",
                    "detail_member_search_user",
                    "detail_member_page_two_user",
                "detail_leader_page_two_user",
            },
        )
        self.assertEqual(
            set(first_payload["scenarios"].keys()),
            {
                "groups-list-shell",
                "groups-list-search-pagination",
                "groups-detail-nested-members",
                "groups-detail-chat-links",
                    "groups-detail-member-search-pagination",
                "groups-detail-leaders-pagination",
            },
        )
        self.assertGreater(len(first_payload["visible_group_aliases"]), 30)
        self.assertEqual(first_payload["scenarios"]["groups-list-shell"]["route_target"], "/groups/")
        self.assertEqual(
            first_payload["scenarios"]["groups-detail-nested-members"]["route_target"],
            reverse("group-detail", args=[first_payload["groups"]["detail_focus_group"]["cn"]]),
        )
        self.assertIn("non_fas_hidden_group", first_payload["groups"])

        visible_group_cns = [
            first_payload["groups"][alias]["cn"]
            for alias in first_payload["visible_group_aliases"]
        ]
        self.assertEqual(visible_group_cns, sorted(visible_group_cns, key=str.lower))

        hidden_group = FreeIPAGroup.get(first_payload["groups"]["non_fas_hidden_group"]["cn"])
        self.assertIsNotNone(hidden_group)
        self.assertFalse(hidden_group.fas_group)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_populates_groups_endpoints_for_list_detail_chat_and_leaders(self) -> None:
        stdout = io.StringIO()
        call_command("groups_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self._login_as_freeipa(payload["actors"]["viewer"]["username"])
        list_response = self.client.get(reverse("api-groups"), HTTP_ACCEPT="application/json")
        search_response = self.client.get(
            reverse("api-groups"),
            {"q": payload["groups"]["search_hit_group"]["cn"]},
            HTTP_ACCEPT="application/json",
        )
        page_two_response = self.client.get(
            reverse("api-groups"),
            {"page": "2"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(search_response.status_code, 200)
        self.assertEqual(page_two_response.status_code, 200)

        visible_group_cns = [payload["groups"][alias]["cn"] for alias in payload["visible_group_aliases"]]
        list_payload = list_response.json()
        search_payload = search_response.json()
        page_two_payload = page_two_response.json()

        self.assertEqual(list_payload["pagination"]["count"], len(visible_group_cns))
        self.assertEqual([item["cn"] for item in list_payload["items"]], visible_group_cns[:30])
        self.assertEqual([item["cn"] for item in search_payload["items"]], [payload["groups"]["search_hit_group"]["cn"]])
        self.assertEqual([item["cn"] for item in page_two_payload["items"]], visible_group_cns[30:])
        self.assertNotIn(
            payload["groups"]["non_fas_hidden_group"]["cn"],
            [item["cn"] for item in list_payload["items"]],
        )

        detail_cn = payload["groups"]["detail_focus_group"]["cn"]
        self._login_as_freeipa(payload["actors"]["sponsor"]["username"])
        info_response = self.client.get(reverse("api-group-detail-info", args=[detail_cn]), HTTP_ACCEPT="application/json")
        members_response = self.client.get(reverse("api-group-detail-members", args=[detail_cn]), HTTP_ACCEPT="application/json")
        leaders_page_one = self.client.get(reverse("api-group-detail-leaders", args=[detail_cn]), HTTP_ACCEPT="application/json")
        leaders_page_two = self.client.get(
            reverse("api-group-detail-leaders", args=[detail_cn]),
            {"page": "2"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(info_response.status_code, 200)
        self.assertEqual(members_response.status_code, 200)
        self.assertEqual(leaders_page_one.status_code, 200)
        self.assertEqual(leaders_page_two.status_code, 200)

        info_payload = info_response.json()["group"]
        members_payload = members_response.json()
        leaders_payload_page_one = leaders_page_one.json()["leaders"]
        leaders_payload_page_two = leaders_page_two.json()["leaders"]

        self.assertEqual(info_payload["member_count"], 3)
        self.assertEqual(
            info_payload["fas_irc_channels"],
            [
                "irc://#wave5-groups",
                "matrix://matrix.org/#wave5-groups",
                "mattermost://chat.almalinux.org/almalinux/channels/wave5-groups",
            ],
        )
        self.assertEqual(
            [item["cn"] for item in members_payload["member_groups"]["items"]],
            [
                payload["groups"]["detail_child_group"]["cn"],
                payload["groups"]["detail_grandchild_group"]["cn"],
            ],
        )
        self.assertEqual(
            [item["username"] for item in members_payload["members"]["items"]],
            [payload["users"]["detail_direct_member"]["username"]],
        )
        self.assertEqual(leaders_payload_page_one["pagination"]["page"], 1)
        self.assertEqual(leaders_payload_page_two["pagination"]["page"], 2)
        self.assertEqual(leaders_payload_page_one["items"][0]["kind"], "group")
        self.assertEqual(
            leaders_payload_page_one["items"][0]["cn"],
            payload["groups"]["detail_leader_group"]["cn"],
        )
        self.assertEqual(
            leaders_payload_page_two["items"][0]["username"],
            payload["users"]["detail_leader_page_two_user"]["username"],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_group_detail_member_search_and_member_pagination_independently_from_leaders(self) -> None:
        stdout = io.StringIO()
        call_command("groups_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        detail_cn = payload["groups"]["detail_member_pagination_group"]["cn"]
        detail_search_username = payload["users"]["detail_member_search_user"]["username"]
        detail_page_two_username = payload["users"]["detail_member_page_two_user"]["username"]

        self._login_as_freeipa(payload["actors"]["sponsor"]["username"])
        members_search_response = self.client.get(
            reverse("api-group-detail-members", args=[detail_cn]),
            {"q": detail_search_username},
            HTTP_ACCEPT="application/json",
        )
        members_page_two_response = self.client.get(
            reverse("api-group-detail-members", args=[detail_cn]),
            {"page": "2"},
            HTTP_ACCEPT="application/json",
        )
        leaders_page_one_response = self.client.get(
            reverse("api-group-detail-leaders", args=[detail_cn]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(members_search_response.status_code, 200)
        self.assertEqual(members_page_two_response.status_code, 200)
        self.assertEqual(leaders_page_one_response.status_code, 200)

        members_search_payload = members_search_response.json()["members"]
        members_page_two_payload = members_page_two_response.json()["members"]
        leaders_page_one_payload = leaders_page_one_response.json()["leaders"]

        self.assertEqual(members_search_payload["q"], detail_search_username)
        self.assertEqual(
            [item["username"] for item in members_search_payload["items"]],
            [detail_search_username],
        )
        self.assertEqual(members_page_two_payload["pagination"]["page"], 2)
        self.assertIn(
            detail_page_two_username,
            [item["username"] for item in members_page_two_payload["items"]],
        )
        self.assertEqual(leaders_page_one_payload["pagination"]["page"], 1)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_required_agreements_for_group_detail_focus_contract(self) -> None:
        stdout = io.StringIO()
        call_command("groups_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        detail_cn = payload["groups"]["detail_focus_group"]["cn"]
        agreement_cn = "wave5-group-access-agreement"

        self._login_as_freeipa(payload["actors"]["sponsor"]["username"])
        info_response = self.client.get(
            reverse("api-group-detail-info", args=[detail_cn]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(info_response.status_code, 200)
        info_payload = info_response.json()["group"]

        self.assertEqual(
            info_payload["required_agreements"],
            [{"cn": agreement_cn, "signed": True}],
        )
        self.assertIn(
            payload["users"]["detail_direct_member"]["username"],
            info_payload["unsigned_usernames"],
        )
        self.assertNotIn(
            payload["actors"]["sponsor"]["username"],
            info_payload["unsigned_usernames"],
        )

        agreement = FreeIPAFASAgreement.get(agreement_cn)
        self.assertIsNotNone(agreement)
        assert agreement is not None
        self.assertIn(detail_cn, agreement.groups)
        self.assertIn(payload["actors"]["sponsor"]["username"], agreement.users)
        self.assertNotIn(payload["users"]["detail_direct_member"]["username"], agreement.users)
        self.assertEqual(agreement.cn, agreement_cn)
        self.assertEqual(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN in agreement.groups, False)

        search_response = self.client.get(
            reverse("global-search"),
            {"q": payload["actors"]["viewer"]["username"]},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(search_response.status_code, 200)
        self.assertIn(
            payload["actors"]["viewer"]["username"],
            [item["username"] for item in search_response.json()["users"]],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_fake_freeipa_supports_user_team_lead_action_round_trip(self) -> None:
        stdout = io.StringIO()
        call_command("groups_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        detail_cn = payload["groups"]["detail_focus_group"]["cn"]
        sponsor_username = payload["actors"]["sponsor"]["username"]
        direct_member_username = payload["users"]["detail_direct_member"]["username"]

        self._login_as_freeipa(sponsor_username)
        promote_response = self.client.post(
            reverse("api-group-action", args=[detail_cn]),
            data=json.dumps({"action": "promote_member", "username": direct_member_username}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(promote_response.status_code, 200)
        self.assertTrue(promote_response.json()["ok"])
        group = FreeIPAGroup.get(detail_cn)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertIn(direct_member_username, group.sponsors)
        self.assertIn(direct_member_username, group.members)

        demote_response = self.client.post(
            reverse("api-group-action", args=[detail_cn]),
            data=json.dumps({"action": "demote_sponsor", "username": direct_member_username}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(demote_response.status_code, 200)
        self.assertTrue(demote_response.json()["ok"])
        group = FreeIPAGroup.get(detail_cn)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertNotIn(direct_member_username, group.sponsors)
        self.assertIn(direct_member_username, group.members)

        stop_sponsoring_response = self.client.post(
            reverse("api-group-action", args=[detail_cn]),
            data=json.dumps({"action": "stop_sponsoring"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(stop_sponsoring_response.status_code, 200)
        self.assertTrue(stop_sponsoring_response.json()["ok"])
        group = FreeIPAGroup.get(detail_cn)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertNotIn(sponsor_username, group.sponsors)