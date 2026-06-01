import json
from typing import Final, override

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import (
    _e2e_group_registry,
    _write_e2e_group_registry,
    is_e2e_fake_freeipa_enabled,
)
from core.freeipa.group import FreeIPAGroup
from core.freeipa.utils import _invalidate_group_cache, _invalidate_groups_list_cache
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_VIEW_USER_DIRECTORY

ACTOR_PASSWORD: Final[str] = "password"
VIEWER_USERNAME: Final[str] = "regular16"
SPONSOR_USERNAME: Final[str] = "regular17"
DETAIL_DIRECT_MEMBER_USERNAME: Final[str] = "regular18"
DETAIL_CHILD_MEMBER_USERNAME: Final[str] = "regular19"
DETAIL_GRANDCHILD_MEMBER_USERNAME: Final[str] = "regular20"
DETAIL_MEMBER_SEARCH_USERNAME: Final[str] = "regular60"
DETAIL_MEMBER_PAGE_TWO_USERNAME: Final[str] = "regular59"
DETAIL_MEMBER_PAGINATION_USERNAMES: Final[tuple[str, ...]] = tuple(
    f"regular{index:02d}" for index in range(18, 61)
)
LEADER_USERNAMES: Final[tuple[str, ...]] = tuple(f"regular{index:02d}" for index in range(21, 50))
PAGE_TWO_LEADER_USERNAME: Final[str] = LEADER_USERNAMES[-1]
DETAIL_REQUIRED_AGREEMENT_CN: Final[str] = "wave5-group-access-agreement"
LEADER_GROUP_DEFINITIONS: Final[tuple[dict[str, object], ...]] = tuple(
    {
        "alias": f"detail_leader_group_{index:02d}",
        "cn": f"wave5-detail-leader-group-{index:02d}",
        "description": f"Wave 5 detail leader group {index:02d}",
        "fas_group": True,
    }
    for index in range(1, 29)
)

GROUP_DEFINITIONS: Final[tuple[dict[str, object], ...]] = (
    {"alias": "alpha_shell_group", "cn": "wave5-alpha-shell-group", "description": "Wave 5 alpha shell group", "fas_group": True},
    {"alias": "beta_shell_group", "cn": "wave5-beta-shell-group", "description": "Wave 5 beta shell group", "fas_group": True},
    {"alias": "search_hit_group", "cn": "wave5-groups-search-hit-group", "description": "Wave 5 deterministic search hit", "fas_group": True},
    {"alias": "visible_list_01", "cn": "wave5-list-01", "description": "Wave 5 visible list 01", "fas_group": True},
    {"alias": "visible_list_02", "cn": "wave5-list-02", "description": "Wave 5 visible list 02", "fas_group": True},
    {"alias": "visible_list_03", "cn": "wave5-list-03", "description": "Wave 5 visible list 03", "fas_group": True},
    {"alias": "visible_list_04", "cn": "wave5-list-04", "description": "Wave 5 visible list 04", "fas_group": True},
    {"alias": "visible_list_05", "cn": "wave5-list-05", "description": "Wave 5 visible list 05", "fas_group": True},
    {"alias": "visible_list_06", "cn": "wave5-list-06", "description": "Wave 5 visible list 06", "fas_group": True},
    {"alias": "visible_list_07", "cn": "wave5-list-07", "description": "Wave 5 visible list 07", "fas_group": True},
    {"alias": "visible_list_08", "cn": "wave5-list-08", "description": "Wave 5 visible list 08", "fas_group": True},
    {"alias": "visible_list_09", "cn": "wave5-list-09", "description": "Wave 5 visible list 09", "fas_group": True},
    {"alias": "visible_list_10", "cn": "wave5-list-10", "description": "Wave 5 visible list 10", "fas_group": True},
    {"alias": "visible_list_11", "cn": "wave5-list-11", "description": "Wave 5 visible list 11", "fas_group": True},
    {"alias": "visible_list_12", "cn": "wave5-list-12", "description": "Wave 5 visible list 12", "fas_group": True},
    {"alias": "visible_list_13", "cn": "wave5-list-13", "description": "Wave 5 visible list 13", "fas_group": True},
    {"alias": "visible_list_14", "cn": "wave5-list-14", "description": "Wave 5 visible list 14", "fas_group": True},
    {"alias": "visible_list_15", "cn": "wave5-list-15", "description": "Wave 5 visible list 15", "fas_group": True},
    {"alias": "visible_list_16", "cn": "wave5-list-16", "description": "Wave 5 visible list 16", "fas_group": True},
    {"alias": "visible_list_17", "cn": "wave5-list-17", "description": "Wave 5 visible list 17", "fas_group": True},
    {"alias": "visible_list_18", "cn": "wave5-list-18", "description": "Wave 5 visible list 18", "fas_group": True},
    {"alias": "visible_list_19", "cn": "wave5-list-19", "description": "Wave 5 visible list 19", "fas_group": True},
    {"alias": "visible_list_20", "cn": "wave5-list-20", "description": "Wave 5 visible list 20", "fas_group": True},
    {"alias": "visible_list_21", "cn": "wave5-list-21", "description": "Wave 5 visible list 21", "fas_group": True},
    {"alias": "visible_list_22", "cn": "wave5-list-22", "description": "Wave 5 visible list 22", "fas_group": True},
    {
        "alias": "detail_focus_group",
        "cn": "wave5-detail-focus-group",
        "description": "Wave 5 detail focus group",
        "fas_group": True,
        "member_user": [DETAIL_DIRECT_MEMBER_USERNAME],
        "member_group": ["wave5-detail-child-group", "wave5-detail-grandchild-group"],
        "membermanager_user": [SPONSOR_USERNAME, PAGE_TWO_LEADER_USERNAME],
        "membermanager_group": [
            "wave5-detail-leader-group",
            *(f"wave5-detail-leader-group-{index:02d}" for index in range(1, 29)),
        ],
        "fasircchannel": [
            "irc://#wave5-groups",
            "matrix://matrix.org/#wave5-groups",
            "mattermost://chat.almalinux.org/almalinux/channels/wave5-groups",
        ],
    },
    {
        "alias": "detail_member_pagination_group",
        "cn": "wave5-detail-member-pagination-group",
        "description": "Wave 5 detail member pagination group",
        "fas_group": True,
        "member_user": list(DETAIL_MEMBER_PAGINATION_USERNAMES),
        "membermanager_user": [SPONSOR_USERNAME],
        "membermanager_group": ["wave5-detail-leader-group"],
    },
    {
        "alias": "detail_child_group",
        "cn": "wave5-detail-child-group",
        "description": "Wave 5 detail child group",
        "fas_group": True,
        "member_user": [DETAIL_CHILD_MEMBER_USERNAME],
        "member_group": ["wave5-detail-grandchild-group"],
    },
    {
        "alias": "detail_grandchild_group",
        "cn": "wave5-detail-grandchild-group",
        "description": "Wave 5 detail grandchild group",
        "fas_group": True,
        "member_user": [DETAIL_GRANDCHILD_MEMBER_USERNAME],
    },
    {
        "alias": "detail_leader_group",
        "cn": "wave5-detail-leader-group",
        "description": "Wave 5 detail leader group",
        "fas_group": True,
    },
    *LEADER_GROUP_DEFINITIONS,
    {"alias": "zulu_shell_group", "cn": "wave5-zulu-shell-group", "description": "Wave 5 zulu shell group", "fas_group": True},
    {"alias": "page_two_group", "cn": "wave5-zz-page-two-group", "description": "Wave 5 page two boundary group", "fas_group": True},
    {"alias": "non_fas_hidden_group", "cn": "wave5-hidden-legacy-group", "description": "Wave 5 hidden non fas group", "fas_group": False},
)

SCENARIO_ALIAS_MATRIX: Final[dict[str, dict[str, object]]] = {
    "groups-list-shell": {
        "actor": VIEWER_USERNAME,
        "aliases": ["alpha_shell_group", "beta_shell_group", "zulu_shell_group"],
        "route_target": "/groups/",
    },
    "groups-list-search-pagination": {
        "actor": VIEWER_USERNAME,
        "aliases": ["search_hit_group", "page_two_group", "non_fas_hidden_group"],
        "route_target": "/groups/",
    },
    "groups-detail-nested-members": {
        "actor": SPONSOR_USERNAME,
        "aliases": ["detail_focus_group", "detail_child_group", "detail_grandchild_group"],
        "route_target_alias": "detail_focus_group",
    },
    "groups-detail-chat-links": {
        "actor": SPONSOR_USERNAME,
        "aliases": ["detail_focus_group"],
        "route_target_alias": "detail_focus_group",
    },
    "groups-detail-member-search-pagination": {
        "actor": SPONSOR_USERNAME,
        "aliases": ["detail_member_pagination_group"],
        "route_target_alias": "detail_member_pagination_group",
    },
    "groups-detail-leaders-pagination": {
        "actor": SPONSOR_USERNAME,
        "aliases": ["detail_focus_group", "detail_leader_group"],
        "route_target_alias": "detail_focus_group",
    },
}


def _group_record(definition: dict[str, object]) -> dict[str, object]:
    fas_group = bool(definition["fas_group"])
    return {
        "cn": [str(definition["cn"])],
        "description": [str(definition["description"])],
        "member_user": list(definition.get("member_user", [])),
        "member_group": list(definition.get("member_group", [])),
        "membermanager_user": list(definition.get("membermanager_user", [])),
        "membermanager_group": list(definition.get("membermanager_group", [])),
        "fasircchannel": list(definition.get("fasircchannel", [])),
        "fasgroup": ["TRUE" if fas_group else "FALSE"],
    }


class Command(BaseCommand):
    help = "Reset the Wave 5 groups E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "groups_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        payload = self._seed_groups()
        self.stdout.write(json.dumps(payload))

    def _seed_groups(self) -> dict[str, object]:
        existing_registry = _e2e_group_registry()
        retained_registry = {
            group_cn: record
            for group_cn, record in existing_registry.items()
            if not FreeIPAGroup(group_cn, record).fas_group
        }

        groups_payload: dict[str, dict[str, object]] = {}
        seeded_registry = dict(retained_registry)
        for definition in GROUP_DEFINITIONS:
            record = _group_record(definition)
            group_cn = str(definition["cn"])
            seeded_registry[group_cn] = record
            groups_payload[str(definition["alias"])] = {
                "cn": group_cn,
                "description": str(definition["description"]),
                "detail_url": reverse("group-detail", args=[group_cn]),
            }

        _write_e2e_group_registry(seeded_registry)
        self._seed_required_agreement()
        self._ensure_sponsor_directory_permission()
        for group_cn in {*existing_registry.keys(), *seeded_registry.keys()}:
            _invalidate_group_cache(group_cn)
        _invalidate_groups_list_cache()

        visible_group_aliases = [
            str(definition["alias"])
            for definition in sorted(
                (definition for definition in GROUP_DEFINITIONS if bool(definition["fas_group"])),
                key=lambda value: str(value["cn"]).lower(),
            )
        ]

        scenarios_payload: dict[str, dict[str, object]] = {}
        for scenario_name, definition in SCENARIO_ALIAS_MATRIX.items():
            route_target = str(definition.get("route_target") or groups_payload[str(definition["route_target_alias"])] ["detail_url"])
            scenarios_payload[scenario_name] = {
                "actor": str(definition["actor"]),
                "aliases": list(definition["aliases"]),
                "destructive": False,
                "route_target": route_target,
            }

        return {
            "scenario": "groups",
            "status": "reset",
            "actors": {
                "viewer": {
                    "username": VIEWER_USERNAME,
                    "password": ACTOR_PASSWORD,
                },
                "sponsor": {
                    "username": SPONSOR_USERNAME,
                    "password": ACTOR_PASSWORD,
                },
            },
            "users": {
                "detail_direct_member": {
                    "username": DETAIL_DIRECT_MEMBER_USERNAME,
                },
                "detail_child_member": {
                    "username": DETAIL_CHILD_MEMBER_USERNAME,
                },
                "detail_grandchild_member": {
                    "username": DETAIL_GRANDCHILD_MEMBER_USERNAME,
                },
                "detail_member_search_user": {
                    "username": DETAIL_MEMBER_SEARCH_USERNAME,
                },
                "detail_member_page_two_user": {
                    "username": DETAIL_MEMBER_PAGE_TWO_USERNAME,
                },
                "detail_leader_page_two_user": {
                    "username": PAGE_TWO_LEADER_USERNAME,
                },
            },
            "visible_group_aliases": visible_group_aliases,
            "groups": groups_payload,
            "scenarios": scenarios_payload,
        }

    def _seed_required_agreement(self) -> None:
        agreement = FreeIPAFASAgreement.get(DETAIL_REQUIRED_AGREEMENT_CN)
        if agreement is None:
            agreement = FreeIPAFASAgreement.create(
                DETAIL_REQUIRED_AGREEMENT_CN,
                description="Wave 5 required agreement",
            )

        detail_focus_group_cn = next(
            str(definition["cn"])
            for definition in GROUP_DEFINITIONS
            if str(definition["alias"]) == "detail_focus_group"
        )

        if detail_focus_group_cn not in agreement.groups:
            agreement.add_group(detail_focus_group_cn)
        if SPONSOR_USERNAME not in agreement.users:
            agreement.add_user(SPONSOR_USERNAME)
        if DETAIL_DIRECT_MEMBER_USERNAME in agreement.users:
            agreement.remove_user(DETAIL_DIRECT_MEMBER_USERNAME)

    def _ensure_sponsor_directory_permission(self) -> None:
        FreeIPAPermissionGrant.objects.update_or_create(
            permission=ASTRA_VIEW_USER_DIRECTORY,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=SPONSOR_USERNAME,
            defaults={},
        )