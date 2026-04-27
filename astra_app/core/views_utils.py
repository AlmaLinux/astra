"""Shared view utilities: authentication helpers, CoC enforcement, pagination.

FreeIPA attribute manipulation helpers live in ``core.ipa_user_attrs``.
"""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.agreements import get_agreement_for_user, has_enabled_agreements
from core.country_codes import country_code_status_from_user_data
from core.logging_extras import current_exception_log_fields
from core.settings_tabs import (
    SETTINGS_TAB_REGISTRY,
    get_settings_tabs,
    is_settings_tab,
    normalize_settings_tab,
)

logger = logging.getLogger(__name__)

_SETTINGS_HIGHLIGHTS: frozenset[str] = frozenset({"country_code"})


def send_mail_url(
    *,
    to_type: str,
    to: str,
    template_name: str,
    extra_context: dict[str, str],
    action_status: str = "",
    reply_to: str | None = None,
) -> str:
    """Build a deep-link URL for the send-mail view.

    Args:
        to_type: Recipient mode for send-mail (for example ``"users"``,
            ``"group"``, ``"manual"``, or ``"csv"``).
        to: Recipient identifier for the selected mode. For ``"csv"`` this
            is typically an empty string because recipients come from session
            CSV data.
        template_name: Email template identifier to preselect.
        extra_context: Additional query parameters that become template
            variables in the send-mail flow.
        action_status: Optional action state marker (for example
            ``"approved"``).
        reply_to: Optional reply-to address list (comma-separated when
            multiple values are needed).

    Returns:
        Absolute path for ``send-mail`` including URL-encoded query params.

    Examples:
        send_mail_url(
            to_type="manual",
            to="user@example.com",
            template_name="org_claim",
            extra_context={"organization_name": "Example Org"},
            reply_to="committee@example.com",
        )
        send_mail_url(
            to_type="csv",
            to="",
            template_name="election_voting_credentials",
            extra_context={"election_committee_email": "vote@example.com"},
        )
    """
    query_params = {
        "type": to_type,
        "to": to,
        "template": template_name,
        **extra_context,
    }
    reply_to_value = str(reply_to or "").strip()
    if reply_to_value:
        query_params["reply_to"] = reply_to_value
    if action_status:
        query_params["action_status"] = action_status
    send_mail_path = reverse("send-mail")
    return f"{send_mail_path}?{urlencode(query_params)}"


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


def normalize_freeipa_username(value: object) -> str:
    return str(value or "").strip().lower()


def normalize_freeipa_group_name(value: object) -> str:
    return str(value or "").strip().lower()


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
        username = normalize_freeipa_username(username_obj)
        if username:
            return username

    if hasattr(user, "get_username"):
        try:
            username_func = user.get_username
        except Exception:
            username_func = None
        if callable(username_func):
            try:
                username = normalize_freeipa_username(username_func())
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
    username = normalize_freeipa_username(session.get("_freeipa_username") if session else None)
    if username or not allow_user_fallback:
        return username
    return try_get_username_from_user(request.user)


MSG_SERVICE_UNAVAILABLE = (
    "This action cannot be completed right now because AlmaLinux Accounts is temporarily unavailable. "
    "Please try again later."
)


def settings_context(active_tab: str, *, show_agreements_tab: bool | None = None) -> dict[str, object]:
    if show_agreements_tab is None:
        try:
            show_agreements_tab = has_enabled_agreements()
        except Exception:
            # Dedicated settings sub-pages should still render even if FreeIPA-backed
            # agreement discovery is temporarily unavailable.
            show_agreements_tab = False

    visible_tabs = list(get_settings_tabs(show_agreements_tab=show_agreements_tab))
    return {
        "active_tab": normalize_settings_tab(active_tab, show_agreements_tab=show_agreements_tab),
        "show_agreements_tab": show_agreements_tab,
        "tabs": [tab.tab_id for tab in SETTINGS_TAB_REGISTRY],
        "settings_tabs": visible_tabs,
    }


def settings_url(
    *,
    tab: str | None = None,
    agreement: str | None = None,
    highlight: str | None = None,
    status: str | None = None,
    return_to: str | None = None,
) -> str:
    """Build canonical settings URL with allowlisted query parameters."""

    params: dict[str, str] = {}

    tab_value = str(tab or "").strip()
    if is_settings_tab(tab_value):
        params["tab"] = tab_value

    agreement_value = str(agreement or "").strip()
    if agreement_value:
        params["agreement"] = agreement_value

    highlight_value = str(highlight or "").strip()
    if highlight_value in _SETTINGS_HIGHLIGHTS:
        params["highlight"] = highlight_value

    status_value = str(status or "").strip()
    if status_value:
        params["status"] = status_value

    # Only allow safe relative paths or the legacy `profile` value.
    return_value = str(return_to or "").strip()
    if return_value == "profile":
        params["return"] = return_value
    elif return_value.startswith("/") and not return_value.startswith("//"):
        params["return"] = return_value

    base = reverse("settings")
    if not params:
        return base
    return f"{base}?{urlencode(params)}"


def agreement_settings_url(agreement_cn: str | None, *, return_to: str | None = None) -> str:
    """Return the canonical settings deep-link for agreement signing."""
    agreement_cn_value = str(agreement_cn or "").strip()
    if not agreement_cn_value:
        return settings_url(tab="agreements", return_to=return_to)
    return settings_url(tab="agreements", agreement=agreement_cn_value, return_to=return_to)


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
    return redirect(settings_url(tab="profile", highlight="country_code", return_to=request.get_full_path()))


def has_signed_coc(username: str) -> bool:
    username = username.strip()
    if not username:
        return False
    agreement_cn = str(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN or "").strip()
    if not agreement_cn:
        return False
    try:
        agreement = get_agreement_for_user(username, agreement_cn)
    except Exception:
        logger.exception("Failed to load Code of Conduct agreement", extra=current_exception_log_fields())
        return False
    if agreement is None:
        return False
    return bool(agreement.signed)


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
    return redirect(agreement_settings_url(agreement_cn, return_to=request.get_full_path()))


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


_DATATABLES_BASE_ALLOWED_PARAMS: frozenset[str] = frozenset(
    {
        "draw",
        "start",
        "length",
        "search[value]",
        "search[regex]",
        "order[0][column]",
        "order[0][dir]",
        "order[0][name]",
        "columns[0][data]",
        "columns[0][name]",
        "columns[0][searchable]",
        "columns[0][orderable]",
        "columns[0][search][value]",
        "columns[0][search][regex]",
    }
)


def parse_datatables_request_base(
    request: HttpRequest,
    *,
    allowed_params: set[str] | frozenset[str] | None = None,
    additional_allowed_params: set[str] | frozenset[str] | None = None,
    allow_cache_buster: bool,
    max_length: int = 100,
) -> tuple[int, int, int]:
    allowed_params_set = set(_DATATABLES_BASE_ALLOWED_PARAMS if allowed_params is None else allowed_params)
    if additional_allowed_params is not None:
        allowed_params_set.update(additional_allowed_params)

    for key in request.GET.keys():
        if key == "_" and allow_cache_buster:
            cache_buster = _normalize_str(request.GET.get(key))
            if not cache_buster.isdigit():
                raise ValueError("Invalid query parameters.")
            continue
        if key not in allowed_params_set:
            raise ValueError("Invalid query parameters.")

    try:
        draw = int(str(request.GET.get("draw") or ""))
        start = int(str(request.GET.get("start") or ""))
        length = int(str(request.GET.get("length") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid query parameters.") from exc

    if draw < 0 or start < 0:
        raise ValueError("Invalid query parameters.")
    if length <= 0 or length > max_length:
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("search[regex]")).lower() == "true":
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("columns[0][search][regex]")).lower() == "true":
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("columns[0][search][value]")).strip():
        raise ValueError("Invalid query parameters.")
    if _normalize_str(request.GET.get("search[value]")).strip():
        raise ValueError("Invalid query parameters.")

    return draw, start, length


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


def _resolve_post_redirect(
    request: HttpRequest,
    *,
    default: str,
    use_referer: bool = False,
) -> str:
    """Resolve a safe redirect URL from POST ``next``, optionally the Referer, or *default*."""
    next_url = str(request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    if use_referer:
        referer = str(request.META.get("HTTP_REFERER") or "").strip()
        candidate = referer or default
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return candidate
    return default
