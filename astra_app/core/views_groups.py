from typing import cast

import requests
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from core.agreements import missing_required_agreements_for_user_in_group, required_agreements_for_group
from core.backends import (
    DegradedFreeIPAUser,
    FreeIPAFASAgreement,
    FreeIPAGroup,
    FreeIPAOperationFailed,
    FreeIPAUser,
    _freeipa_circuit_open,
)
from core.forms_groups import GroupEditForm
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required
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


def groups(request: HttpRequest) -> HttpResponse:
    q = _normalize_str(request.GET.get("q"))
    page_number = _normalize_str(request.GET.get("page")) or None

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

    groups_list = FreeIPAGroup.all()
    groups_filtered = [g for g in groups_list if g.fas_group and _matches_query(g, q)]
    groups_sorted = sorted(groups_filtered, key=_sort_key)

    for g in groups_sorted:
        g.member_count = g.member_count_recursive()

    _, page_url_prefix = build_page_url_prefix(request.GET, page_param="page")
    page_ctx = paginate_and_build_context(groups_sorted, page_number, 30, page_url_prefix=page_url_prefix)

    return render(
        request,
        "core/groups.html",
        {
            "q": q,
            **page_ctx,
            "groups": page_ctx["page_obj"].object_list,
        },
    )


def group_detail(request: HttpRequest, name: str) -> HttpResponse:
    cn = _normalize_str(name)
    if not cn:
        raise Http404("Group not found")

    group = FreeIPAGroup.get(cn)
    if not group or not group.fas_group:
        raise Http404("Group not found")

    q = _normalize_str(request.GET.get("q"))

    def _is_fas_group(cn: str) -> bool:
        group_obj = FreeIPAGroup.get(cn)
        return bool(group_obj and group_obj.fas_group)

    member_count = group.member_count_recursive(fas_only=True)

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

    sponsor_groups_lower = {g.lower() for g in sponsor_groups}
    user_groups_lower = {g.lower() for g in user_groups}
    is_sponsor = (username in sponsors) or bool(sponsor_groups_lower & user_groups_lower)
    is_member = username in members

    sponsor_groups_list = sorted((g for g in sponsor_groups if _is_fas_group(g)), key=lambda s: s.lower())
    sponsors_list = sorted(sponsors, key=lambda s: s.lower())
    promotable_members = sorted((members - sponsors), key=lambda s: s.lower())

    required_agreement_cns = required_agreements_for_group(cn)
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
                    "detail_url": agreement_settings_url(agreement_cn),
                    "list_url": agreement_settings_url(None),
                }
            )

        for u in sorted(members | sponsors, key=lambda s: s.lower()):
            for agreement_cn in required_agreement_cns:
                if u not in agreement_user_sets.get(agreement_cn, set()):
                    unsigned_usernames.add(u)
                    break

    if request.method == "POST":
        if isinstance(request.user, DegradedFreeIPAUser) or _freeipa_circuit_open():
            messages.error(
                request,
                MSG_SERVICE_UNAVAILABLE,
            )
            return redirect("group-detail", name)

        action = _normalize_str(request.POST.get("action")).lower()

        if action == "leave":
            if not is_member:
                messages.info(request, "You are not a member of this group.")
                return redirect("group-detail", name)
            try:
                cast(FreeIPAUser, request.user).remove_from_group(cn)
                messages.success(request, "You have left the group.")
            except requests.exceptions.ConnectionError:
                messages.error(
                    request,
                    MSG_SERVICE_UNAVAILABLE,
                )
            except Exception:
                messages.error(request, "Failed to leave group due to an internal error.")
            return redirect("group-detail", name)

        if action == "stop_sponsoring":
            if not is_sponsor:
                messages.info(request, "You are not a Team Lead of this group.")
                return redirect("group-detail", name)
            try:
                group.remove_sponsor(username)
                messages.success(request, "You are no longer a Team Lead of this group.")
            except requests.exceptions.ConnectionError:
                messages.error(
                    request,
                    MSG_SERVICE_UNAVAILABLE,
                )
            except Exception:
                messages.error(request, "Failed to update sponsor status due to an internal error.")
            return redirect("group-detail", name)

        if action in {"add_member", "remove_member"}:
            if not is_sponsor:
                messages.error(request, "Only Team Leads can manage group members.")
                return redirect("group-detail", name)

            target = _normalize_str(request.POST.get("username"))
            if not target:
                messages.error(request, "Please provide a username.")
                return redirect("group-detail", name)

            if target == username and action == "add_member":
                messages.error(request, "You can't add yourself to a group.")
                return redirect("group-detail", name)

            if action == "add_member":
                missing = missing_required_agreements_for_user_in_group(target, cn)
                if missing:
                    messages.error(
                        request,
                        "User must sign required agreement(s) before joining: " + ", ".join(missing),
                    )
                    return redirect("group-detail", name)
                try:
                    group.add_member(target)
                    messages.success(request, f"Added {target} to the group.")
                except FreeIPAOperationFailed as e:
                    messages.error(request, str(e))
                except requests.exceptions.ConnectionError:
                    messages.error(
                        request,
                        MSG_SERVICE_UNAVAILABLE,
                    )
                except Exception:
                    messages.error(request, "Failed to add member due to an internal error.")
                return redirect("group-detail", name)

            if action == "remove_member":
                try:
                    group.remove_member(target)
                    messages.success(request, f"Removed {target} from the group.")
                except FreeIPAOperationFailed as e:
                    messages.error(request, str(e))
                except requests.exceptions.ConnectionError:
                    messages.error(
                        request,
                        MSG_SERVICE_UNAVAILABLE,
                    )
                except Exception:
                    messages.error(request, "Failed to remove member due to an internal error.")
                return redirect("group-detail", name)

        if action == "promote_member":
            if not is_sponsor:
                messages.error(request, "Only Team Leads can manage group members.")
                return redirect("group-detail", name)

            target = _normalize_str(request.POST.get("username"))
            if not target:
                messages.error(request, "Please provide a username.")
                return redirect("group-detail", name)

            if target in sponsors:
                messages.info(request, f"{target} is already a Team Lead of this group.")
                return redirect("group-detail", name)

            if target not in members:
                messages.error(request, "User must be a member before being promoted to Team Lead.")
                return redirect("group-detail", name)

            try:
                group.add_sponsor(target)
                messages.success(request, f"Promoted {target} to Team Lead.")
            except FreeIPAOperationFailed as e:
                messages.error(request, str(e))
            except requests.exceptions.ConnectionError:
                messages.error(
                    request,
                    MSG_SERVICE_UNAVAILABLE,
                )
            except Exception:
                messages.error(request, "Failed to update sponsor status due to an internal error.")
            return redirect("group-detail", name)

        if action == "demote_sponsor":
            if not is_sponsor:
                messages.error(request, "Only Team Leads can manage group members.")
                return redirect("group-detail", name)

            target = _normalize_str(request.POST.get("username"))
            if not target:
                messages.error(request, "Please provide a username.")
                return redirect("group-detail", name)

            if target == username:
                messages.info(request, "Use the Team membership box to stop being a Team Lead.")
                return redirect("group-detail", name)

            if target not in sponsors:
                messages.error(request, "User is not a Team Lead of this group.")
                return redirect("group-detail", name)

            try:
                group.remove_sponsor(target)
                messages.success(request, f"Removed {target} as a Team Lead.")
            except FreeIPAOperationFailed as e:
                messages.error(request, str(e))
            except requests.exceptions.ConnectionError:
                messages.error(
                    request,
                    MSG_SERVICE_UNAVAILABLE,
                )
            except Exception:
                messages.error(request, "Failed to update sponsor status due to an internal error.")
            return redirect("group-detail", name)

    return render(
        request,
        "core/group_detail.html",
        {
            "group": group,
            "member_count": member_count,
            "q": q,
            "is_member": is_member,
            "is_sponsor": is_sponsor,
            "current_username": username,
            "sponsors_list": sponsors_list,
            "sponsor_groups_list": sponsor_groups_list,
            "promotable_members": promotable_members,
            "required_agreements": required_agreements,
            "unsigned_usernames": sorted(unsigned_usernames, key=lambda s: s.lower()),
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

    if request.method == "POST":
        form = GroupEditForm(request.POST)
        if form.is_valid():
            group.description = form.cleaned_data["description"]
            group.fas_url = form.cleaned_data["fas_url"] or None
            group.fas_mailing_list = form.cleaned_data["fas_mailing_list"] or None
            group.fas_discussion_url = form.cleaned_data["fas_discussion_url"] or None
            group.fas_irc_channels = list(form.cleaned_data["fas_irc_channels"])

            try:
                group.save()
                messages.success(request, "Saved group info.")
                return redirect("group-detail", cn)
            except requests.exceptions.ConnectionError:
                messages.error(
                    request,
                    MSG_SERVICE_UNAVAILABLE,
                )
            except Exception:
                messages.error(request, "Failed to save group info due to an internal error.")
    else:
        form = GroupEditForm(
            initial={
                "description": group.description or "",
                "fas_url": group.fas_url or "",
                "fas_mailing_list": group.fas_mailing_list or "",
                "fas_discussion_url": group.fas_discussion_url or "",
                "fas_irc_channels": "\n".join(group.fas_irc_channels or []),
            }
        )

    return render(
        request,
        "core/group_edit.html",
        {
            "group": group,
            "form": form,
        },
    )
