import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.agreements import (
    has_enabled_agreements,
    list_agreements_for_user,
    missing_required_agreements_for_user_in_group,
)
from core.country_codes import country_code_status_from_user_data
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import _data_get, _first, _get_full_user, _value_to_text
from core.membership import (
    build_pending_request_context,
    compute_membership_requestability_context,
    expiring_soon_cutoff,
    get_membership_request_eligibility,
    get_valid_memberships,
    resolve_request_ids_by_membership_type,
)
from core.membership_notifications import membership_extend_url
from core.models import MembershipRequest
from core.permissions import can_view_user_directory
from core.views_utils import _normalize_str, agreement_settings_url, get_username, settings_url

logger = logging.getLogger(__name__)


def _profile_context_for_user(
    request: HttpRequest,
    *,
    fu: FreeIPAUser,
    is_self: bool,
) -> dict[str, object]:
    data = fu._user_data

    fas_tz_name = _first(data, "fasTimezone", "")
    tz_name = ""
    tzinfo: ZoneInfo | None = None
    if fas_tz_name:
        try:
            tzinfo = ZoneInfo(fas_tz_name)
            tz_name = fas_tz_name
        except Exception:
            tz_name = ""
            tzinfo = None

    now_local = timezone.localtime(timezone.now(), timezone=tzinfo) if tzinfo else None

    groups_list = fu.groups_list

    # Only show FAS groups on the public profile page.
    # Using `FreeIPAGroup.all()` keeps this one cached call vs. per-group lookups.
    fas_groups = [g for g in FreeIPAGroup.all() if g.fas_group]
    fas_cns = {g.cn for g in fas_groups if g.cn}

    member_groups = {g for g in groups_list if g in fas_cns}

    sponsor_groups: set[str] = set()
    for g in fas_groups:
        cn = g.cn
        if not cn:
            continue
        if fu.username in g.sponsors:
            sponsor_groups.add(cn)

    visible_groups = sorted(member_groups | sponsor_groups, key=str.lower)
    groups = [
        {
            "cn": cn,
            "role": "Sponsor" if cn in sponsor_groups else "Member",
        }
        for cn in visible_groups
    ]

    show_agreements = has_enabled_agreements()
    if show_agreements:
        agreements_for_user = list_agreements_for_user(
            fu.username,
            user_groups=groups_list,
            include_disabled=False,
            applicable_only=False,
        )

        agreements = sorted([a.cn for a in agreements_for_user if a.signed], key=str.lower)

        coc_agreement = next(
            (
                a
                for a in agreements_for_user
                if a.cn == settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN and a.enabled
            ),
            None,
        )
        coc_signed = bool(coc_agreement and coc_agreement.signed)
        coc_settings_url = (
            agreement_settings_url(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN, return_to="profile")
            if is_self
            else None
        )

        missing_required: dict[str, set[str]] = {}
        for group_cn in sorted(member_groups, key=str.lower):
            for agreement_cn in missing_required_agreements_for_user_in_group(fu.username, group_cn):
                missing_required.setdefault(agreement_cn, set()).add(group_cn)

        missing_agreements = [
            {
                "cn": agreement_cn,
                "required_by": sorted(required_by, key=str.lower),
                "settings_url": agreement_settings_url(agreement_cn)
                if is_self
                else None,
            }
            for agreement_cn, required_by in sorted(missing_required.items(), key=lambda kv: kv[0].lower())
        ]
    else:
        agreements = []
        missing_agreements = []
        coc_signed = True
        coc_settings_url = None

    def _as_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if _normalize_str(v)]
        if isinstance(value, str):
            s = value.strip()
            return [s] if s else []
        return []

    irc_nicks = _as_list(_data_get(data, "fasIRCNick", []))
    website_urls = _as_list(_data_get(data, "fasWebsiteUrl", []))
    rss_urls = _as_list(_data_get(data, "fasRssUrl", []))
    gpg_keys = _as_list(_data_get(data, "fasGPGKeyId", []))
    ssh_keys = _as_list(_data_get(data, "ipasshpubkey", []))

    profile_avatar_user: object = fu

    membership_request_url = reverse("membership-request")
    valid_memberships = get_valid_memberships(username=fu.username)
    membership_eligibility = get_membership_request_eligibility(username=fu.username)

    has_individual_membership = any(m.membership_type.category.is_individual for m in valid_memberships)

    membership_type_ids = {m.membership_type_id for m in valid_memberships}
    request_id_by_membership_type_id = resolve_request_ids_by_membership_type(
        username=fu.username,
        membership_type_ids=membership_type_ids,
    )
    expiring_soon_by = expiring_soon_cutoff()

    personal_pending_requests_qs = list(
        MembershipRequest.objects.select_related("membership_type")
        .filter(
            requested_username=fu.username,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
        .order_by("requested_at")
    )

    personal_pending_context = build_pending_request_context(
        personal_pending_requests_qs,
        is_organization=False,
    )
    pending_request_category_ids = personal_pending_context.category_ids

    requestability_context = compute_membership_requestability_context(
        username=fu.username,
        eligibility=membership_eligibility,
        held_category_ids={membership.membership_type.category_id for membership in valid_memberships},
    )
    requestable_codes_by_category = requestability_context.requestable_codes_by_category

    memberships: list[dict[str, object]] = []
    for membership in valid_memberships:
        membership_category_id = membership.membership_type.category_id
        expires_at = membership.expires_at
        is_expiring_soon = bool(expires_at and expires_at <= expiring_soon_by)
        has_pending_request_in_category = membership_category_id in pending_request_category_ids
        memberships.append(
            {
                "membership_type": membership.membership_type,
                "created_at": membership.created_at,
                "expires_at": expires_at,
                "is_expiring_soon": is_expiring_soon,
                "has_pending_request_in_category": has_pending_request_in_category,
                "extend_url": membership_extend_url(
                    membership_type_code=membership.membership_type.code,
                    base_url="",
                ),
                "can_request_tier_change": (
                    any(
                        code != membership.membership_type.code
                        for code in requestable_codes_by_category.get(membership_category_id, set())
                    )
                    and not has_pending_request_in_category
                ),
                "request_id": request_id_by_membership_type_id.get(membership.membership_type_id),
            }
        )

    org_pending_requests_qs = list(
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .filter(
            requested_username="",
            requested_organization__representative=fu.username,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
        .order_by("requested_at")
    )

    personal_pending_requests = personal_pending_context.entries
    org_pending_requests = build_pending_request_context(
        org_pending_requests_qs,
        is_organization=True,
    ).entries

    pending_requests: list[dict[str, object]] = sorted(
        [*personal_pending_requests, *org_pending_requests],
        key=lambda item: (item.get("requested_at"), item.get("request_id")),
    )

    membership_action_required_requests: list[dict[str, object]] = [
        r for r in pending_requests if r.get("status") == MembershipRequest.Status.on_hold
    ]

    membership_can_request_any = requestability_context.membership_can_request_any

    email_is_blacklisted = False
    if is_self and fu.email:
        # Local import: this app uses django-ses to track delivery-related blacklisting.
        from django_ses.models import BlacklistedEmail

        email_is_blacklisted = BlacklistedEmail.objects.filter(email__iexact=fu.email).exists()

    country_status = country_code_status_from_user_data(data)

    account_setup_required_actions: list[dict[str, str]] = []
    account_setup_recommended_actions: list[dict[str, str]] = []

    has_open_membership_request = bool(personal_pending_requests)
    has_rejected_membership_request = MembershipRequest.objects.filter(
        requested_username=fu.username,
        status=MembershipRequest.Status.rejected,
    ).exists()

    if is_self:
        if not coc_signed and coc_settings_url:
            account_setup_required_actions.append(
                {
                    "id": "coc-not-signed-alert",
                    "label": f"Sign the {settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN}",
                    "url": coc_settings_url,
                    "url_label": "Review & sign",
                }
            )

        if not country_status.is_valid:
            account_setup_required_actions.append(
                {
                    "id": "country-code-missing-alert",
                    "label": "Add a valid ISO 3166-1 alpha-2 country code",
                    "url": settings_url(tab="profile", highlight="country_code"),
                    "url_label": "Set country code",
                }
            )

        if email_is_blacklisted:
            account_setup_required_actions.append(
                {
                    "id": "email-blacklisted-alert",
                    "label": "We're having trouble delivering emails",
                    "url": settings_url(tab="emails"),
                    "url_label": "Update your email",
                }
            )

        personal_action_required = [
            r for r in personal_pending_requests if r.get("status") == MembershipRequest.Status.on_hold
        ]
        if personal_action_required:
            request_id = int(personal_action_required[0]["request_id"])
            account_setup_required_actions.append(
                {
                    "id": "membership-action-required-alert",
                    "label": "Help us review your membership request",
                    "url": reverse("membership-request-self", args=[request_id]),
                    "url_label": "Add details",
                }
            )

        org_action_required = [
            r for r in org_pending_requests if r.get("status") == MembershipRequest.Status.on_hold
        ]
        if org_action_required:
            request_id = int(org_action_required[0]["request_id"])
            account_setup_required_actions.append(
                {
                    "id": "sponsorship-action-required-alert",
                    "label": "Help us review your sponsorship request",
                    "url": reverse("membership-request-self", args=[request_id]),
                    "url_label": "Add details",
                }
            )

        if (
            (not has_individual_membership)
            and membership_can_request_any
            and (not has_open_membership_request)
            and (not has_rejected_membership_request)
        ):
            account_setup_recommended_actions.append(
                {
                    "id": "membership-request-recommended-alert",
                    "label": "Request an individual membership",
                    "url": membership_request_url,
                    "url_label": "Request a membership",
                }
            )

    account_setup_required_is_rfi = any(
        action["id"] in {"membership-action-required-alert", "sponsorship-action-required-alert"}
        for action in account_setup_required_actions
    )

    return {
        "fu": fu,
        "profile_avatar_user": profile_avatar_user,
        "is_self": is_self,
        "email_is_blacklisted": email_is_blacklisted,
        "country_code": country_status.code,
        "country_code_missing_or_invalid": not country_status.is_valid,
        "account_setup_required_actions": account_setup_required_actions,
        "account_setup_required_is_rfi": account_setup_required_is_rfi,
        "account_setup_recommended_actions": account_setup_recommended_actions,
        "membership_request_url": membership_request_url,
        "membership_can_request_any": membership_can_request_any,
        "memberships": memberships,
        "membership_pending_requests": pending_requests,
        "membership_action_required_requests": membership_action_required_requests,
        "groups": groups,
        "agreements": agreements,
        "missing_agreements": missing_agreements,
        "timezone_name": tz_name,
        "current_time": now_local,
        "pronouns": _value_to_text(_data_get(data, "fasPronoun", "")),
        "locale": _first(data, "fasLocale", "") or "",
        "irc_nicks": irc_nicks,
        "website_urls": website_urls,
        "rss_urls": rss_urls,
        "rhbz_email": _first(data, "fasRHBZEmail", "") or "",
        "github_username": _first(data, "fasGitHubUsername", "") or "",
        "gitlab_username": _first(data, "fasGitLabUsername", "") or "",
        "gpg_keys": gpg_keys,
        "ssh_keys": ssh_keys,
    }


def home(request: HttpRequest) -> HttpResponse:
    username = get_username(request)
    if not username:
        messages.error(request, "Unable to determine your username.")
        return redirect("login")
    return redirect("user-profile", username=username)


def user_profile(request: HttpRequest, username: str) -> HttpResponse:
    username = _normalize_str(username)
    if not username:
        raise Http404("User not found")

    viewer_username = get_username(request)
    logger.debug("User profile view: username=%s viewer=%s", username, viewer_username)

    fu = _get_full_user(username)
    if not fu:
        raise Http404("User not found")

    is_self = username == viewer_username

    context = _profile_context_for_user(request, fu=fu, is_self=is_self)
    return render(request, "core/user_profile.html", context)


def users(request: HttpRequest) -> HttpResponse:
    if not can_view_user_directory(request.user):
        raise Http404

    users_list = FreeIPAUser.all()
    q = _normalize_str(request.GET.get("q"))

    return render(
        request,
        "core/users.html",
        {
            "q": q,
            # Pass the full list; `core_user_grid.user_grid` handles filtering + pagination.
            "users": users_list,
        },
    )
