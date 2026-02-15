"""Shared view utilities: authentication helpers, CoC enforcement, pagination.

FreeIPA attribute manipulation helpers live in ``core.ipa_user_attrs``.
"""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from core.agreements import has_enabled_agreements
from core.backends import FreeIPAFASAgreement
from core.country_codes import country_code_status_from_user_data

logger = logging.getLogger(__name__)


def require_post_or_404(request: HttpRequest, *, message: str = "Not found") -> None:
    """Raise a 404 when an endpoint is accessed with a non-POST method.

    Astra has historical behavior where these endpoints intentionally return
    404 (not 405) for non-POST access.
    """

    if request.method != "POST":
        raise Http404(message)


def post_only_404[**P, R](view_func: Callable[P, R]) -> Callable[P, R]:
    """Decorator variant of ``require_post_or_404`` for view functions."""

    @wraps(view_func)
    def _wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        request = args[0]
        require_post_or_404(request)
        return view_func(*args, **kwargs)

    return _wrapped


def try_get_username_from_user(user: object) -> str:
    """Best-effort username extraction for user-like objects.

    This is used in template tags and utility code that may receive
    FreeIPA users, Django users, or test doubles.
    """
    if user is None:
        return ""

    username: str | None = None
    if hasattr(user, "username"):
        try:
            username_obj = user.username
        except Exception:
            username_obj = ""
        username = str(username_obj or "").strip()
        if username:
            return username

    if hasattr(user, "get_username"):
        try:
            username_func = user.get_username
        except Exception:
            username_func = None
        if callable(username_func):
            try:
                username = str(username_func() or "").strip()
            except Exception:
                username = ""
            if username:
                return username

    return ""


def get_username(request: HttpRequest, *, allow_user_fallback: bool = True) -> str:
    """Extract the authenticated username from session (authoritative) or user object.

    The FreeIPA auth backend stores the canonical username in the session
    at login time. This is the most reliable source since the Django user
    object may be loaded from cache with a different casing or format.
    """
    session = request.session if hasattr(request, "session") else None
    username = str((session.get("_freeipa_username") if session else None) or "").strip()
    if username or not allow_user_fallback:
        return username
    return try_get_username_from_user(request.user)


MSG_SERVICE_UNAVAILABLE = (
    "This action cannot be completed right now because AlmaLinux Accounts is temporarily unavailable. "
    "Please try again later."
)


def settings_context(active_tab: str) -> dict[str, object]:
    return {
        "active_tab": active_tab,
        "show_agreements_tab": has_enabled_agreements(),
    }


def agreement_settings_url(agreement_cn: str | None) -> str:
    """Return the canonical settings deep-link for agreement signing."""
    agreement_cn_value = str(agreement_cn or "").strip()
    if not agreement_cn_value:
        return f"{reverse('settings')}#agreements"
    return f"{reverse('settings')}?agreement={quote(agreement_cn_value)}#agreements"


def block_action_without_country_code(
    request: HttpRequest,
    *,
    user_data: dict | None,
    action_label: str,
) -> HttpResponse | None:
    """Enforce that a user has a valid country code before performing an action.

    This is a legal/compliance requirement used across multiple flows (e.g.
    settings updates, membership requests).
    """

    status = country_code_status_from_user_data(user_data)
    if status.is_valid:
        return None

    messages.error(
        request,
        f"A valid country code is required before you can {action_label}. Please set it on the Profile tab.",
    )
    return redirect(f"{reverse('settings')}#profile")


def _coc_agreement_for_user(username: str) -> FreeIPAFASAgreement | None:
    agreement_cn = str(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN or "").strip()
    if not agreement_cn:
        return None
    try:
        return FreeIPAFASAgreement.get(agreement_cn)
    except Exception:
        logger.exception("Failed to load Code of Conduct agreement")
        return None


def has_signed_coc(username: str) -> bool:
    username = username.strip()
    if not username:
        return False
    agreement = _coc_agreement_for_user(username)
    if agreement is None or not agreement.enabled:
        return False
    return username in set(agreement.users)


def block_action_without_coc(
    request: HttpRequest,
    *,
    username: str,
    action_label: str,
) -> HttpResponse | None:
    """Enforce signing the Community Code of Conduct before actions."""

    if has_signed_coc(username):
        return None

    agreement_cn = str(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN or "").strip()
    label = agreement_cn or "Community Code of Conduct"
    messages.error(request, f"You must sign the {label} before you can {action_label}.")
    return redirect(agreement_settings_url(agreement_cn))


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def pagination_window(paginator: Paginator, page_number: int) -> tuple[list[int], bool, bool]:
    """Compute a sliding page-number window for paginated views.

    Returns (page_numbers, show_first, show_last).
    """
    total_pages = paginator.num_pages
    if total_pages <= 10:
        return list(range(1, total_pages + 1)), False, False

    start = max(1, page_number - 2)
    end = min(total_pages, page_number + 2)
    page_numbers = list(range(start, end + 1))
    show_first = 1 not in page_numbers
    show_last = total_pages not in page_numbers
    return page_numbers, show_first, show_last


def paginate_and_build_context(
    items,
    page_param: str | None,
    per_page: int,
    *,
    page_url_prefix: str = "?page=",
) -> dict[str, Any]:
    """Build a complete pagination context dict for templates using _pagination.html.

    Returns a dict with: paginator, page_obj, is_paginated, page_numbers,
    show_first, show_last, page_url_prefix.
    """
    paginator = Paginator(items, per_page)
    page_obj = paginator.get_page(page_param)
    page_numbers, show_first, show_last = pagination_window(paginator, page_obj.number)
    return {
        "paginator": paginator,
        "page_obj": page_obj,
        "is_paginated": paginator.num_pages > 1,
        "page_numbers": page_numbers,
        "show_first": show_first,
        "show_last": show_last,
        "page_url_prefix": page_url_prefix,
    }


def build_page_url_prefix(query: object, *, page_param: str = "page") -> tuple[str, str]:
    """Build ``(base_query, page_url_prefix)`` while retaining non-page params."""
    if hasattr(query, "copy"):
        params = query.copy()
    elif isinstance(query, dict):
        params = query.copy()
    else:
        params = {}

    if hasattr(params, "pop"):
        params.pop(page_param, None)

    if hasattr(params, "urlencode"):
        base_query = str(params.urlencode())
    else:
        base_query = urlencode(params, doseq=True)

    page_url_prefix = f"?{base_query}&{page_param}=" if base_query else f"?{page_param}="
    return base_query, page_url_prefix


def build_url_for_page(
    base_url: str,
    *,
    query: object,
    page_param: str,
    page_value: int | str,
) -> str:
    """Build a page URL preserving all query params except for the target page value."""
    if hasattr(query, "copy"):
        params = query.copy()
    elif isinstance(query, dict):
        params = query.copy()
    else:
        params = {}

    params[page_param] = str(page_value)
    if hasattr(params, "urlencode"):
        encoded = str(params.urlencode())
    else:
        encoded = urlencode(params, doseq=True)
    return f"{base_url}?{encoded}" if encoded else base_url
