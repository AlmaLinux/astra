from __future__ import annotations

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


SEND_MAIL_PERMISSIONS: frozenset[str] = frozenset({ASTRA_ADD_SEND_MAIL})


P = ParamSpec("P")
R = TypeVar("R", bound=HttpResponse)


def json_permission_required(permission: str) -> Callable[[Callable[P, R]], Callable[P, HttpResponse]]:
    """Decorator for JSON endpoints that require a single Django permission.

    This returns a JSON 403 response instead of redirecting or rendering HTML.
    Authentication is enforced by LoginRequiredMiddleware.
    """

    def decorator(view_func: Callable[P, R]) -> Callable[P, HttpResponse]:
        @wraps(view_func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            if not args:
                return JsonResponse({"error": "Permission denied."}, status=403)

            request = args[0]
            if not isinstance(request, HttpRequest):
                return JsonResponse({"error": "Permission denied."}, status=403)

            try:
                allowed = request.user.has_perm(permission)
            except Exception:
                allowed = False

            if not allowed:
                return JsonResponse({"error": "Permission denied."}, status=403)

            return view_func(*args, **kwargs)

        return wrapper

    return decorator


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
    for perm in MEMBERSHIP_PERMISSIONS:
        try:
            if user.has_perm(perm):
                return True
        except Exception:
            # Be defensive: template context processors may be invoked with
            # partial stubs or AnonymousUser-like objects.
            continue

    return False
