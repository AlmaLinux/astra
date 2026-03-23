from django.http import HttpRequest, JsonResponse

from core.freeipa.group import FreeIPAGroup
from core.freeipa_directory import search_freeipa_users
from core.models import Organization
from core.permissions import can_view_user_directory
from core.views_utils import _normalize_str


def global_search(request: HttpRequest) -> JsonResponse:
    q = _normalize_str(request.GET.get("q"))
    if not q:
        return JsonResponse({"users": [], "groups": []})

    q_lower = q.lower()
    has_directory_access = can_view_user_directory(request.user)
    matched_users = search_freeipa_users(query=q, limit=100) if has_directory_access else []
    matched_usernames = {
        str(user.username)
        for user in matched_users
        if str(user.username).strip()
    }

    groups_out: list[dict[str, str]] = []
    for g in FreeIPAGroup.all():
        if not g.fas_group:
            continue
        if not g.cn:
            continue

        members: list[str] = []
        sponsors: list[str] = []
        # Some tests patch FreeIPAGroup objects with lightweight stubs that may
        # omit membership attributes.
        if hasattr(g, "members"):
            members = [str(member).strip() for member in g.members if str(member).strip()]
        if hasattr(g, "sponsors"):
            sponsors = [str(sponsor).strip() for sponsor in g.sponsors if str(sponsor).strip()]

        group_member_match = bool(matched_usernames & (set(members) | set(sponsors)))
        name_match = q_lower in g.cn.lower()
        description_match = bool(g.description and q_lower in g.description.lower())

        if not (name_match or description_match or group_member_match):
            continue

        groups_out.append({"cn": g.cn, "description": g.description})
        if len(groups_out) >= 7:
            break

    response_payload: dict[str, list[dict]] = {
        "groups": groups_out,
    }

    if has_directory_access:
        users_out = [
            {"username": str(user.username), "full_name": str(user.full_name)}
            for user in matched_users[:7]
        ]
        response_payload["users"] = users_out

        # Local import avoids creating a hard import cycle between view modules.
        from core.views_organizations import _filter_organization_queryset_by_search

        orgs_out = list(
            _filter_organization_queryset_by_search(
                Organization.objects.all(),
                q=q,
                can_manage_memberships=False,
                matched_representative_usernames=matched_usernames,
            )
            .values("id", "name")
            .order_by("name")[:7]
        )
        response_payload["orgs"] = orgs_out

    # Keep output deterministic for tests/UI.
    groups_out.sort(key=lambda x: x["cn"].lower())

    return JsonResponse(response_payload)
