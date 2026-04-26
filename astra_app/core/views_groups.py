import json
from typing import Any, cast

import requests
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from core.agreements import missing_required_agreements_for_user_in_group, required_agreements_for_group
from core.api_pagination import paginate_detail_items, serialize_pagination
from core.avatar_providers import resolve_avatar_urls_for_users
from core.forms_groups import GroupEditForm
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.circuit_breaker import _freeipa_circuit_open
from core.freeipa.exceptions import FreeIPAOperationFailed
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import DegradedFreeIPAUser, FreeIPAUser
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required
from core.templatetags._user_helpers import try_get_full_name
from core.views_utils import (
    MSG_SERVICE_UNAVAILABLE,
    _normalize_str,
    agreement_settings_url,
    build_page_url_prefix,
    get_username,
    paginate_and_build_context,
)


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def group_search(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    q_lower = q.lower()

    results: list[dict[str, str]] = []
    for g in FreeIPAGroup.all():
        if q_lower:
            if q_lower not in g.cn.lower() and q_lower not in (g.description or "").lower():
                continue

        text = g.cn
        desc = str(g.description or "").strip()
        if desc:
            text = f"{g.cn} — {desc}"

        results.append({"id": g.cn, "text": text})
        if len(results) >= 20:
            break

    results.sort(key=lambda r: r["id"].lower())
    return JsonResponse({"results": results})


def _sort_key(group: FreeIPAGroup) -> str:
    return group.cn.lower()


def _matches_query(group: FreeIPAGroup, query: str) -> bool:
    if not query:
        return True
    query_lower = query.lower()
    if query_lower in group.cn.lower():
        return True
    desc = (group.description or "").lower()
    return query_lower in desc


def _groups_page_context(request: HttpRequest) -> dict[str, object]:
    q = _normalize_str(request.GET.get("q"))
    page_number = _normalize_str(request.GET.get("page")) or None
    groups_list = FreeIPAGroup.all()
    groups_filtered = [g for g in groups_list if g.fas_group and _matches_query(g, q)]
    groups_sorted = sorted(groups_filtered, key=_sort_key)

    for group in groups_sorted:
        group.member_count = group.member_count_recursive()

    _, page_url_prefix = build_page_url_prefix(request.GET, page_param="page")
    page_ctx = paginate_and_build_context(groups_sorted, page_number, 30, page_url_prefix=page_url_prefix)
    return {
        "q": q,
        "groups": page_ctx["page_obj"].object_list,
        **page_ctx,
    }


def _is_fas_group(cn: str) -> bool:
    group_obj = FreeIPAGroup.get(cn)
    return bool(group_obj and group_obj.fas_group)


def _group_membership_context(request: HttpRequest, group: FreeIPAGroup) -> dict[str, object]:
    username = get_username(request)
    try:
        sponsors = set(group.sponsors)
    except AttributeError:
        sponsors = set()

    try:
        sponsor_groups = set(group.sponsor_groups)
    except AttributeError:
        sponsor_groups = set()

    try:
        members = set(group.members)
    except AttributeError:
        members = set()

    user_groups: set[str] = set()
    if isinstance(request.user, FreeIPAUser):
        user_groups = set(request.user.groups_list)

    sponsor_groups_lower = {group_name.lower() for group_name in sponsor_groups}
    user_groups_lower = {group_name.lower() for group_name in user_groups}
    is_sponsor = (username in sponsors) or bool(sponsor_groups_lower & user_groups_lower)
    is_member = username in members

    sponsor_groups_list = sorted((group_name for group_name in sponsor_groups if _is_fas_group(group_name)), key=lambda value: value.lower())
    sponsors_list = sorted(sponsors, key=lambda value: value.lower())
    promotable_members = sorted((members - sponsors), key=lambda value: value.lower())

    required_agreement_cns = required_agreements_for_group(group.cn)
    required_agreements: list[dict[str, object]] = []
    unsigned_usernames: set[str] = set()
    if required_agreement_cns:
        agreement_user_sets: dict[str, set[str]] = {}
        for agreement_cn in required_agreement_cns:
            agreement = FreeIPAFASAgreement.get(agreement_cn)
            users = set(agreement.users) if agreement else set()
            agreement_user_sets[agreement_cn] = users

        for agreement_cn in required_agreement_cns:
            users_signed = agreement_user_sets.get(agreement_cn, set())
            required_agreements.append(
                {
                    "cn": agreement_cn,
                    "signed": username in users_signed,
                }
            )

        for member_username in sorted(members | sponsors, key=lambda value: value.lower()):
            for agreement_cn in required_agreement_cns:
                if member_username not in agreement_user_sets.get(agreement_cn, set()):
                    unsigned_usernames.add(member_username)
                    break

    return {
        "username": username,
        "sponsors": sponsors,
        "sponsor_groups": sponsor_groups,
        "members": members,
        "is_sponsor": is_sponsor,
        "is_member": is_member,
        "sponsors_list": sponsors_list,
        "sponsor_groups_list": sponsor_groups_list,
        "promotable_members": promotable_members,
        "required_agreements": required_agreements,
        "unsigned_usernames": sorted(unsigned_usernames, key=lambda value: value.lower()),
    }


def _group_edit_payload(group: FreeIPAGroup) -> dict[str, object]:
    return {
        "cn": group.cn,
        "description": group.description or "",
        "fas_url": group.fas_url or "",
        "fas_mailing_list": group.fas_mailing_list or "",
        "fas_discussion_url": group.fas_discussion_url or "",
        "fas_irc_channels": list(group.fas_irc_channels or []),
    }


def _serialize_group_user_items(usernames: list[str]) -> dict[str, dict[str, str]]:
    users_by_username: dict[str, FreeIPAUser] = {}
    user_objects: list[FreeIPAUser] = []

    for username in usernames:
        if username in users_by_username:
            continue
        user = FreeIPAUser.get(username)
        if user is None:
            continue
        users_by_username[username] = user
        user_objects.append(user)

    avatar_url_by_username, _avatar_resolution_count, _avatar_fallback_count = resolve_avatar_urls_for_users(
        user_objects,
        width=50,
        height=50,
    )

    items_by_username: dict[str, dict[str, str]] = {}
    for username in usernames:
        user = users_by_username.get(username)
        items_by_username[username] = {
            "username": username,
            "full_name": try_get_full_name(user) if user is not None else "",
            "avatar_url": avatar_url_by_username.get(username, ""),
        }

    return items_by_username


def _serialize_group_user_list_items(
    usernames: list[str],
    *,
    leader_usernames: set[str] | None = None,
) -> list[dict[str, object]]:
    serialized_users = _serialize_group_user_items(usernames)
    items: list[dict[str, object]] = []
    for username in usernames:
        serialized = {
            **serialized_users.get(username, {"username": username, "full_name": "", "avatar_url": ""}),
        }
        if leader_usernames is not None:
            serialized["is_leader"] = username in leader_usernames
        items.append(serialized)
    return items


def _serialize_group_leader_items(leader_entries: list[dict[str, str]]) -> list[dict[str, object]]:
    leader_usernames = [entry["username"] for entry in leader_entries if entry["kind"] == "user"]
    serialized_users = _serialize_group_user_items(leader_usernames)

    items: list[dict[str, object]] = []
    for entry in leader_entries:
        if entry["kind"] == "group":
            items.append({"kind": "group", "cn": entry["cn"]})
            continue

        username = entry["username"]
        items.append(
            {
                "kind": "user",
                **serialized_users.get(username, {"username": username, "full_name": "", "avatar_url": ""}),
            }
        )

    return items


def _group_or_404(name: str) -> FreeIPAGroup:
    cn = _normalize_str(name)
    if not cn:
        raise Http404("Group not found")

    group = FreeIPAGroup.get(cn)
    if not group or not group.fas_group:
        raise Http404("Group not found")

    return group


def _group_info_payload(group: FreeIPAGroup, membership_ctx: dict[str, object]) -> dict[str, object]:
    return {
        "cn": group.cn,
        "description": group.description or "",
        "fas_url": group.fas_url or "",
        "fas_mailing_list": group.fas_mailing_list or "",
        "fas_discussion_url": group.fas_discussion_url or "",
        "fas_irc_channels": list(group.fas_irc_channels or []),
        "member_count": group.member_count_recursive(fas_only=True),
        "is_member": membership_ctx["is_member"],
        "is_sponsor": membership_ctx["is_sponsor"],
        "required_agreements": membership_ctx["required_agreements"],
        "unsigned_usernames": membership_ctx["unsigned_usernames"],
    }


def _json_error(message: str, *, status: int) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def groups(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/groups.html",
        {
            "group_detail_url_template": reverse("group-detail", args=["placeholder-group"]).replace(
                "placeholder-group", "__group_name__"
            ),
        },
    )


def group_detail(request: HttpRequest, name: str) -> HttpResponse:
    cn = _normalize_str(name)
    if not cn:
        raise Http404("Group not found")

    group = FreeIPAGroup.get(cn)
    if not group or not group.fas_group:
        raise Http404("Group not found")

    return render(
        request,
        "core/group_detail.html",
        {
            "group": group,
            "group_detail_url_template": reverse("group-detail", args=["placeholder-group"]).replace(
                "placeholder-group", "__group_name__"
            ),
            "group_edit_url_template": reverse("group-edit", args=["placeholder-group"]).replace(
                "placeholder-group", "__group_name__"
            ),
            "agreement_detail_url_template": agreement_settings_url("placeholder-agreement").replace(
                "placeholder-agreement", "__agreement_cn__"
            ),
            "agreements_list_url": agreement_settings_url(None),
        },
    )


def group_edit(request: HttpRequest, name: str) -> HttpResponse:
    cn = _normalize_str(name)
    if not cn:
        raise Http404("Group not found")

    group = FreeIPAGroup.get(cn)
    if not group or not group.fas_group:
        raise Http404("Group not found")

    username = get_username(request)
    sponsors = set(group.sponsors)
    sponsor_groups = set(group.sponsor_groups)
    user_groups: set[str] = set()
    if isinstance(request.user, FreeIPAUser):
        user_groups = set(request.user.groups_list)

    sponsor_groups_lower = {g.lower() for g in sponsor_groups}
    user_groups_lower = {g.lower() for g in user_groups}
    is_sponsor = (username in sponsors) or bool(sponsor_groups_lower & user_groups_lower)
    if not is_sponsor:
        raise PermissionDenied("Only sponsors can edit group info.")

    return render(
        request,
        "core/group_edit.html",
        {"group": group},
    )


@login_required
@require_GET
def groups_api(request: HttpRequest) -> JsonResponse:
    page_context = _groups_page_context(request)
    groups_list = page_context["groups"]
    payload = {
        "q": page_context["q"],
        "items": [
            {
                "cn": group.cn,
                "description": group.description or "",
                "member_count": group.member_count,
            }
            for group in groups_list
        ],
        "pagination": serialize_pagination(page_context),
    }
    return JsonResponse(payload)


@login_required
@require_GET
def group_detail_info_api(request: HttpRequest, name: str) -> JsonResponse:
    group = _group_or_404(name)
    membership_ctx = _group_membership_context(request, group)

    return JsonResponse({"group": _group_info_payload(group, membership_ctx)})


@login_required
@require_GET
def group_detail_leaders_api(request: HttpRequest, name: str) -> JsonResponse:
    group = _group_or_404(name)
    membership_ctx = _group_membership_context(request, group)

    leader_entries = [
        {"kind": "group", "cn": sponsor_group}
        for sponsor_group in membership_ctx["sponsor_groups_list"]
    ] + [
        {"kind": "user", "username": sponsor_username}
        for sponsor_username in membership_ctx["sponsors_list"]
    ]

    leaders_page_items, leaders_page_ctx = paginate_detail_items(request, cast(list[object], leader_entries))
    items = _serialize_group_leader_items(cast(list[dict[str, str]], leaders_page_items))

    return JsonResponse(
        {
            "leaders": {
                "items": items,
                "pagination": serialize_pagination(leaders_page_ctx),
            }
        }
    )


@login_required
@require_GET
def group_detail_members_api(request: HttpRequest, name: str) -> JsonResponse:
    group = _group_or_404(name)
    membership_ctx = _group_membership_context(request, group)
    members_q = _normalize_str(request.GET.get("q"))

    members_all = sorted(membership_ctx["members"], key=lambda value: value.lower())
    members_filtered = [
        username
        for username in members_all
        if (not members_q) or (members_q.lower() in username.lower())
    ]
    members_page_items, members_page_ctx = paginate_detail_items(request, cast(list[object], members_filtered))
    sponsor_usernames = set(membership_ctx["sponsors_list"])

    return JsonResponse(
        {
            "members": {
                "q": members_q,
                "items": _serialize_group_user_list_items(cast(list[str], members_page_items), leader_usernames=sponsor_usernames),
                "pagination": serialize_pagination(members_page_ctx),
            }
        }
    )


def _json_dict_from_body(request: HttpRequest) -> dict[str, Any]:
    try:
        raw = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


@login_required
@require_http_methods(["POST"])
def group_action_api(request: HttpRequest, name: str) -> JsonResponse:
    cn = _normalize_str(name)
    if not cn:
        raise Http404("Group not found")

    group = FreeIPAGroup.get(cn)
    if not group or not group.fas_group:
        raise Http404("Group not found")

    if isinstance(request.user, DegradedFreeIPAUser) or _freeipa_circuit_open():
        return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)

    payload = _json_dict_from_body(request)
    action = _normalize_str(payload.get("action")).lower()
    target = _normalize_str(payload.get("username"))

    membership_ctx = _group_membership_context(request, group)
    username = membership_ctx["username"]
    is_sponsor = bool(membership_ctx["is_sponsor"])
    is_member = bool(membership_ctx["is_member"])
    sponsors = membership_ctx["sponsors"]
    members = membership_ctx["members"]

    if action == "leave":
        if not is_member:
            return _json_error("You are not a member of this group.", status=400)
        try:
            cast(FreeIPAUser, request.user).remove_from_group(cn)
        except requests.exceptions.ConnectionError:
            return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
        except Exception:
            return _json_error("Failed to leave group due to an internal error.", status=500)
        return JsonResponse({"ok": True, "message": "You have left the group."})

    if action == "stop_sponsoring":
        if not is_sponsor:
            return _json_error("You are not a Team Lead of this group.", status=403)
        try:
            group.remove_sponsor(username)
        except requests.exceptions.ConnectionError:
            return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
        except Exception:
            return _json_error("Failed to update sponsor status due to an internal error.", status=500)
        return JsonResponse({"ok": True, "message": "You are no longer a Team Lead of this group."})

    if action in {"add_member", "remove_member"}:
        if not is_sponsor:
            return _json_error("Only Team Leads can manage group members.", status=403)
        if not target:
            return _json_error("Please provide a username.", status=400)
        if action == "add_member" and target == username:
            return _json_error("You can't add yourself to a group.", status=400)

        if action == "add_member":
            missing = missing_required_agreements_for_user_in_group(target, cn)
            if missing:
                return _json_error(
                    "User must sign required agreement(s) before joining: " + ", ".join(missing),
                    status=400,
                )
            try:
                group.add_member(target)
            except FreeIPAOperationFailed as exc:
                return _json_error(str(exc), status=400)
            except requests.exceptions.ConnectionError:
                return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
            except Exception:
                return _json_error("Failed to add member due to an internal error.", status=500)
            return JsonResponse({"ok": True, "message": f"Added {target} to the group."})

        try:
            group.remove_member(target)
        except FreeIPAOperationFailed as exc:
            return _json_error(str(exc), status=400)
        except requests.exceptions.ConnectionError:
            return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
        except Exception:
            return _json_error("Failed to remove member due to an internal error.", status=500)
        return JsonResponse({"ok": True, "message": f"Removed {target} from the group."})

    if action == "promote_member":
        if not is_sponsor:
            return _json_error("Only Team Leads can manage group members.", status=403)
        if not target:
            return _json_error("Please provide a username.", status=400)
        if target in sponsors:
            return _json_error(f"{target} is already a Team Lead of this group.", status=400)
        if target not in members:
            return _json_error("User must be a member before being promoted to Team Lead.", status=400)
        try:
            group.add_sponsor(target)
        except FreeIPAOperationFailed as exc:
            return _json_error(str(exc), status=400)
        except requests.exceptions.ConnectionError:
            return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
        except Exception:
            return _json_error("Failed to update sponsor status due to an internal error.", status=500)
        return JsonResponse({"ok": True, "message": f"Promoted {target} to Team Lead."})

    if action == "demote_sponsor":
        if not is_sponsor:
            return _json_error("Only Team Leads can manage group members.", status=403)
        if not target:
            return _json_error("Please provide a username.", status=400)
        if target == username:
            return _json_error("Use the Team membership box to stop being a Team Lead.", status=400)
        if target not in sponsors:
            return _json_error("User is not a Team Lead of this group.", status=400)
        try:
            group.remove_sponsor(target)
        except FreeIPAOperationFailed as exc:
            return _json_error(str(exc), status=400)
        except requests.exceptions.ConnectionError:
            return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
        except Exception:
            return _json_error("Failed to update sponsor status due to an internal error.", status=500)
        return JsonResponse({"ok": True, "message": f"Removed {target} as a Team Lead."})

    return _json_error("Invalid action.", status=400)


@login_required
@require_http_methods(["GET", "PUT"])
def group_edit_api(request: HttpRequest, name: str) -> JsonResponse:
    cn = _normalize_str(name)
    if not cn:
        raise Http404("Group not found")

    group = FreeIPAGroup.get(cn)
    if not group or not group.fas_group:
        raise Http404("Group not found")

    membership_ctx = _group_membership_context(request, group)
    if not membership_ctx["is_sponsor"]:
        return _json_error("Only sponsors can edit group info.", status=403)

    if request.method == "GET":
        return JsonResponse({"group": _group_edit_payload(group)})

    payload = _json_dict_from_body(request)
    if not payload:
        return _json_error("Invalid request payload.", status=400)

    form = GroupEditForm(
        {
            "description": str(payload.get("description") or ""),
            "fas_url": str(payload.get("fas_url") or ""),
            "fas_mailing_list": str(payload.get("fas_mailing_list") or ""),
            "fas_discussion_url": str(payload.get("fas_discussion_url") or ""),
            "fas_irc_channels": str(payload.get("fas_irc_channels") or ""),
        }
    )
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    group.description = form.cleaned_data["description"]
    group.fas_url = form.cleaned_data["fas_url"] or None
    group.fas_mailing_list = form.cleaned_data["fas_mailing_list"] or None
    group.fas_discussion_url = form.cleaned_data["fas_discussion_url"] or None
    group.fas_irc_channels = list(form.cleaned_data["fas_irc_channels"])

    try:
        group.save()
    except requests.exceptions.ConnectionError:
        return _json_error(MSG_SERVICE_UNAVAILABLE, status=503)
    except Exception:
        return _json_error("Failed to save group info due to an internal error.", status=500)

    return JsonResponse({"ok": True, "group": _group_edit_payload(group)})
