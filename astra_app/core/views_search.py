from django.http import HttpRequest, JsonResponse

from core.backends import FreeIPAGroup, FreeIPAUser
from core.permissions import ASTRA_VIEW_USER_DIRECTORY
from core.views_utils import _normalize_str


def global_search(request: HttpRequest) -> JsonResponse:
    q = _normalize_str(request.GET.get("q"))
    if not q:
        return JsonResponse({"users": [], "groups": []})

    q_lower = q.lower()

    has_directory_access = request.user.has_perm(ASTRA_VIEW_USER_DIRECTORY)

    users_out: list[dict[str, str]] = []
    if has_directory_access:
        for u in FreeIPAUser.all():
            if not u.username:
                continue

            full_name = u.full_name
            if q_lower not in u.username.lower() and q_lower not in full_name.lower():
                continue

            users_out.append({"username": u.username, "full_name": full_name})
            if len(users_out) >= 7:
                break

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

    # Keep output deterministic for tests/UI.
    users_out.sort(key=lambda x: x["username"].lower())
    groups_out.sort(key=lambda x: x["cn"].lower())

    return JsonResponse({"users": users_out, "groups": groups_out})
