from collections.abc import Callable, Collection
from functools import wraps
from typing import ParamSpec, TypeVar

from django.http import HttpRequest, HttpResponse, JsonResponse

ASTRA_ADD_MEMBERSHIP = "astra.add_membership"
ASTRA_CHANGE_MEMBERSHIP = "astra.change_membership"
ASTRA_DELETE_MEMBERSHIP = "astra.delete_membership"
ASTRA_VIEW_MEMBERSHIP = "astra.view_membership"

ASTRA_ADD_SEND_MAIL = "astra.add_sendmail"

ASTRA_ADD_ELECTION = "astra.add_election"

ASTRA_VIEW_USER_DIRECTORY = "astra.view_user_directory"

MEMBERSHIP_PERMISSIONS: frozenset[str] = frozenset(
    {
        ASTRA_ADD_MEMBERSHIP,
        ASTRA_CHANGE_MEMBERSHIP,
        ASTRA_DELETE_MEMBERSHIP,
        ASTRA_VIEW_MEMBERSHIP,
    }
)

MEMBERSHIP_MANAGE_PERMISSIONS: frozenset[str] = frozenset(
    {
        ASTRA_ADD_MEMBERSHIP,
        ASTRA_CHANGE_MEMBERSHIP,
        ASTRA_DELETE_MEMBERSHIP,
    }
)


SEND_MAIL_PERMISSIONS: frozenset[str] = frozenset({ASTRA_ADD_SEND_MAIL})

MEMBERSHIP_REVIEW_PERMISSION_MAP: dict[str, str] = {
    "membership_can_add": ASTRA_ADD_MEMBERSHIP,
    "membership_can_change": ASTRA_CHANGE_MEMBERSHIP,
    "membership_can_delete": ASTRA_DELETE_MEMBERSHIP,
    "membership_can_view": ASTRA_VIEW_MEMBERSHIP,
    "send_mail_can_add": ASTRA_ADD_SEND_MAIL,
}


P = ParamSpec("P")
R = TypeVar("R", bound=HttpResponse)


def json_permission_required(permission: str) -> Callable[[Callable[P, R]], Callable[P, HttpResponse]]:
    """Decorator for JSON endpoints that require a single Django permission.

    This returns a JSON 403 response instead of redirecting or rendering HTML.
    Authentication is enforced by LoginRequiredMiddleware.
    """
    return json_permission_required_any({permission})


def json_permission_required_any(permissions: Collection[str]) -> Callable[[Callable[P, R]], Callable[P, HttpResponse]]:
    """Decorator for JSON endpoints that accept any one of several permissions."""

    perms = tuple(permissions)
    if not perms:
        raise ValueError("permissions must not be empty")

    def decorator(view_func: Callable[P, R]) -> Callable[P, HttpResponse]:
        @wraps(view_func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            if not args:
                return JsonResponse({"error": "Permission denied."}, status=403)

            request = args[0]
            if not isinstance(request, HttpRequest):
                return JsonResponse({"error": "Permission denied."}, status=403)

            allowed = False
            for perm in perms:
                try:
                    if request.user.has_perm(perm):
                        allowed = True
                        break
                except Exception:
                    continue

            if not allowed:
                return JsonResponse({"error": "Permission denied."}, status=403)

            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def has_any_membership_permission(user: object) -> bool:
    return _has_any_permission(user=user, permissions=MEMBERSHIP_PERMISSIONS)


def has_any_membership_manage_permission(user: object) -> bool:
    return _has_any_permission(user=user, permissions=MEMBERSHIP_MANAGE_PERMISSIONS)


def can_view_user_directory(user: object) -> bool:
    return _has_permission(user=user, permission=ASTRA_VIEW_USER_DIRECTORY)


def membership_review_permissions(user: object) -> dict[str, bool]:
    return {
        key: _has_permission(user=user, permission=perm)
        for key, perm in MEMBERSHIP_REVIEW_PERMISSION_MAP.items()
    }


def _has_permission(*, user: object, permission: str) -> bool:
    try:
        return bool(user.has_perm(permission))
    except Exception:
        # Template context processors and tests may pass user-like stubs.
        return False


def _has_any_permission(*, user: object, permissions: Collection[str]) -> bool:
    for perm in permissions:
        if _has_permission(user=user, permission=perm):
            return True
    return False
