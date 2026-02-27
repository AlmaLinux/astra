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

    groups_out: list[dict[str, str]] = []
    for g in FreeIPAGroup.all():
        if not g.fas_group:
            continue
        if not g.cn:
            continue

        if q_lower not in g.cn.lower() and (not g.description or q_lower not in g.description.lower()):
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
            for user in search_freeipa_users(query=q, limit=7)
        ]
        response_payload["users"] = users_out

        orgs_out = list(
            Organization.objects.filter(name__icontains=q).values("id", "name").order_by("name")[:7]
        )
        response_payload["orgs"] = orgs_out

    # Keep output deterministic for tests/UI.
    groups_out.sort(key=lambda x: x["cn"].lower())

    return JsonResponse(response_payload)
