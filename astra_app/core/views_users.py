import logging
from datetime import datetime
from typing import cast
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_GET

from core.agreements import (
    has_enabled_agreements,
    list_agreements_for_user,
    missing_required_agreements_for_user_in_group,
)
from core.api_pagination import serialize_pagination
from core.avatar_providers import resolve_avatar_urls_for_users
from core.country_codes import country_code_status_from_user_data, country_name_from_code
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.freeipa_directory import snapshot_freeipa_users
from core.ipa_user_attrs import _data_get, _first, _get_full_user, _split_list_field, _value_to_text
from core.membership import (
    build_pending_request_context,
    compute_membership_requestability_context,
    expiring_soon_cutoff,
    get_membership_request_eligibility,
    get_valid_memberships,
    resolve_request_ids_by_membership_type,
)
from core.membership_notifications import membership_extend_url
from core.models import MembershipRequest, MembershipType
from core.permissions import can_view_user_directory, membership_review_permissions
from core.templatetags._grid_tag_utils import parse_grid_query
from core.templatetags._user_helpers import try_get_full_name
from core.templatetags.core_dict import membership_tier_class
from core.templatetags.core_user_grid import build_user_grid_page
from core.views_utils import (
    _normalize_str,
    agreement_settings_url,
    get_username,
    normalize_freeipa_username,
    settings_url,
    try_get_username_from_user,
)

logger = logging.getLogger(__name__)


def _is_membership_committee_viewer(request: HttpRequest) -> bool:
    committee_group = str(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP or "").strip()
    if not committee_group:
        return False

    # Request user can be a test double without FreeIPA group metadata.
    try:
        viewer_groups = request.user.groups_list
    except AttributeError:
        viewer_groups = None

    if viewer_groups is not None:
        return committee_group in viewer_groups

    # Fall back to the authenticated FreeIPA user when the request object does
    # not expose group metadata. That keeps committee-only profile visibility
    # working in admin-style request doubles and other lightweight auth stubs.
    viewer_username = get_username(request)
    if not viewer_username:
        return False

    try:
        viewer = FreeIPAUser.get(viewer_username)
    except Exception:
        return False

    if viewer is None:
        return False

    return committee_group in viewer.groups_list


def _profile_context_for_user(
    request: HttpRequest,
    *,
    fu: FreeIPAUser,
    is_self: bool,
    viewer_is_membership_committee: bool,
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

    def _host_for_url(url: str) -> str | None:
        """Return lowercase hostname, resilient to scheme-less inputs."""

        s = url.strip()
        if not s:
            return None

        parsed = urlsplit(s)
        host = parsed.hostname
        if not host:
            parsed = urlsplit(f"//{s}")
            host = parsed.hostname

        if not host:
            return None

        host = host.lower().removesuffix(".")
        return host

    # Domain-based "clues" for common social profile URLs.
    # Keep minimal and conservative; unknown domains remain in Website.
    social_platform_domains: dict[str, tuple[str, ...]] = {
        "bluesky": ("bsky.app", "bsky.social"),
        # Mastodon instances commonly live under many different domains.
        # We special-case hostnames that contain a `mastodon` DNS label (e.g. mastodon.social,
        # www.mastodon.social, mastodon.example.org) in _social_platform_key_for_url.
        "mastodon": ("mstdn.social", "fosstodon.org", "hachyderm.io", "mstdn.jp", "meshed.cloud", "mas.to", "big.sound-city.dk"),
        # "X" is still commonly shared as twitter.com.
        "x": ("x.com", "twitter.com"),
        "linkedin": ("linkedin.com", "lnkd.in"),
        "facebook": ("facebook.com",),
        "instagram": ("instagram.com", "instagr.am"),
        "youtube": ("youtube.com", "youtu.be"),
        "reddit": ("reddit.com",),
        "tiktok": ("tiktok.com",),
        "signal": ("signal.me", "signal.group"),
    }

    social_platform_specs: dict[str, dict[str, str]] = {
        "bluesky": {"label": "Bluesky", "title": "Bluesky URLs", "icon": "fab fa-bluesky"},
        "mastodon": {"label": "Mastodon", "title": "Mastodon URLs", "icon": "fab fa-mastodon"},
        "x": {"label": "X (Twitter)", "title": "X (Twitter) URLs", "icon": "fab fa-x-twitter"},
        "linkedin": {"label": "LinkedIn", "title": "LinkedIn URLs", "icon": "fab fa-linkedin"},
        "facebook": {"label": "Facebook", "title": "Facebook URLs", "icon": "fab fa-facebook"},
        "instagram": {"label": "Instagram", "title": "Instagram URLs", "icon": "fab fa-instagram"},
        "youtube": {"label": "YouTube", "title": "YouTube URLs", "icon": "fab fa-youtube"},
        "reddit": {"label": "Reddit", "title": "Reddit URLs", "icon": "fab fa-reddit"},
        "tiktok": {"label": "TikTok", "title": "TikTok URLs", "icon": "fab fa-tiktok"},
        "signal": {"label": "Signal", "title": "Signal URLs", "icon": "fab fa-signal-messenger"},
    }

    def _safe_external_href(url: str) -> str | None:
        """Return a safe external href for user-provided URLs.

        Security/robustness:
        - Treat scheme-less inputs like "example.com/path" as external by normalizing to https://.
        - Treat protocol-relative inputs like "//example.com/path" as external by normalizing to https:.
        - Block non-http(s) schemes (mailto:, javascript:, ftp:, ...): return None so templates render
          plain text instead of a clickable link.
        """

        s = url.strip()
        if not s:
            return None

        if s.startswith("//"):
            parsed = urlsplit(s)
            return f"https:{s}" if parsed.hostname else None

        parsed = urlsplit(s)
        if parsed.scheme:
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                return s
            return None

        parsed = urlsplit(f"//{s}")
        return f"https://{s}" if parsed.hostname else None

    def _social_display_text(platform_key: str, url: str) -> str:
        host = _host_for_url(url)
        fallback = host or url.strip() or ""

        s = url.strip()
        if not s:
            return fallback

        parsed = urlsplit(s)
        if not parsed.hostname:
            parsed = urlsplit(f"//{s}")

        path = parsed.path or ""
        segments = [seg for seg in path.split("/") if seg]

        match platform_key:
            case "bluesky":
                # Common forms: https://bsky.app/profile/<handle> or /profile/<handle>
                if "profile" in segments:
                    idx = segments.index("profile")
                    if idx + 1 < len(segments):
                        handle = segments[idx + 1].lstrip("@").strip()
                        if handle:
                            return f"@{handle}"
                if host and host.endswith(".bsky.social") and host != "bsky.social":
                    return f"@{host}"
                return host or fallback
            case "x":
                if segments:
                    user = segments[0].lstrip("@").strip()
                    if user:
                        return f"@{user}"
                return host or fallback
            case "instagram" | "tiktok":
                if segments:
                    user = segments[0].lstrip("@").strip()
                    if user:
                        return f"@{user}"
                return host or fallback
            case "reddit":
                if len(segments) >= 2 and segments[0] in {"u", "user"}:
                    name = segments[1].strip()
                    if name:
                        return f"u/{name}"
                if len(segments) >= 2 and segments[0] == "r":
                    name = segments[1].strip()
                    if name:
                        return f"r/{name}"
                return host or fallback
            case "mastodon":
                if host:
                    at_segment = next((seg for seg in segments if seg.startswith("@") and seg.strip("@")), "")
                    if at_segment:
                        user = at_segment.lstrip("@").strip()
                        if user:
                            return f"@{user}@{host}"
                return host or fallback
            case "signal":
                # Do not attempt to surface phone numbers or group IDs.
                return host or "Signal link"
            case "youtube":
                if segments and segments[0].startswith("@"):
                    user = segments[0].lstrip("@").strip()
                    if user:
                        return f"@{user}"
                return host or fallback
            case "linkedin":
                if len(segments) >= 2 and segments[0] == "in":
                    name = segments[1].strip()
                    if name:
                        return name
                return host or fallback
            case _:
                return host or fallback

    def _social_platform_key_for_url(url: str) -> str | None:
        host = _host_for_url(url)
        if not host:
            return None

        # Mastodon: accept any hostname that includes a `mastodon` label (mastodon.*)
        # without maintaining an ever-growing allowlist of `mastodon.<tld>` instances.
        host_labels = [p for p in host.split(".") if p]
        if any(label == "mastodon" for label in host_labels[:-1]):
            return "mastodon"

        for platform_key, domains in social_platform_domains.items():
            if any(host == domain or host.endswith(f".{domain}") for domain in domains):
                return platform_key
        return None

    irc_nicks = _as_list(_data_get(data, "fasIRCNick", []))
    # FreeIPA generally stores `fasWebsiteUrl` as a multi-valued attribute, but some
    # legacy/hand-edited records may contain multiple URLs in a single value.
    fas_website_urls: list[str] = []
    for value in _as_list(_data_get(data, "fasWebsiteUrl", [])):
        if "\n" in value or "," in value:
            fas_website_urls.extend(_split_list_field(value))
        else:
            fas_website_urls.append(value)
    social_urls_by_platform: dict[str, list[str]] = {platform_key: [] for platform_key in social_platform_domains}
    website_url_values: list[str] = []
    for url in fas_website_urls:
        platform_key = _social_platform_key_for_url(url)
        if platform_key:
            social_urls_by_platform[platform_key].append(url)
        else:
            website_url_values.append(url)

    social_profiles: list[dict[str, object]] = []
    for platform_key in social_platform_domains:
        platform_urls = social_urls_by_platform[platform_key]
        if not platform_urls:
            continue

        spec = social_platform_specs[platform_key]
        urls: list[dict[str, str | None]] = [
            {"href": _safe_external_href(url), "text": _social_display_text(platform_key, url)}
            for url in platform_urls
        ]
        social_profiles.append(
            {
                "platform": platform_key,
                "label": spec["label"],
                "title": spec["title"],
                "icon": spec["icon"],
                "urls": urls,
            }
        )

    website_urls: list[dict[str, str | None]] = [
        {"href": _safe_external_href(url), "text": url.strip()} for url in website_url_values if url.strip()
    ]

    rss_urls: list[dict[str, str | None]] = [
        {"href": _safe_external_href(url), "text": url.strip()}
        for url in _as_list(_data_get(data, "fasRssUrl", []))
        if url.strip()
    ]
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
    profile_country = "Not provided"
    if country_status.is_valid and country_status.code:
        profile_country = country_name_from_code(country_status.code)

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
                    "agreement_cn": settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN,
                    "url_label": "Review & sign",
                }
            )

        if not country_status.is_valid:
            account_setup_required_actions.append(
                {
                    "id": "country-code-missing-alert",
                    "label": "Add a valid ISO 3166-1 alpha-2 country code",
                    "url_label": "Set country code",
                }
            )

        if email_is_blacklisted:
            account_setup_required_actions.append(
                {
                    "id": "email-blacklisted-alert",
                    "label": "We're having trouble delivering your emails: your address may have bounced or been marked as spam",
                    "url_label": "Update your email address",
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
                    "request_id": str(request_id),
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
                    "request_id": str(request_id),
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
                    "url_label": "Request a membership",
                }
            )

    account_setup_required_is_rfi = any(
        action["id"] in {"membership-action-required-alert", "sponsorship-action-required-alert"}
        for action in account_setup_required_actions
    )

    # Test helpers and some stubs use user-like objects without this attribute.
    profile_is_private = fu.fas_is_private if hasattr(fu, "fas_is_private") else False
    show_membership_card = (not profile_is_private) or is_self or viewer_is_membership_committee

    profile_email = str(fu.email or "").strip()
    if profile_is_private and not is_self and viewer_is_membership_committee and not profile_email:
        try:
            full_profile_user = FreeIPAUser.get(fu.username, respect_privacy=False)
        except Exception:
            full_profile_user = None
        if full_profile_user is not None:
            profile_email = str(full_profile_user.email or "").strip()

    return {
        "fu": fu,
        "profile_email": profile_email,
        "profile_avatar_user": profile_avatar_user,
        "is_self": is_self,
        "email_is_blacklisted": email_is_blacklisted,
        "country_code": country_status.code,
        "country_code_missing_or_invalid": not country_status.is_valid,
        "account_setup_required_actions": account_setup_required_actions,
        "account_setup_required_is_rfi": account_setup_required_is_rfi,
        "account_setup_recommended_actions": account_setup_recommended_actions,
        "membership_request_url": membership_request_url,
        "show_membership_card": show_membership_card,
        "viewer_is_membership_committee": viewer_is_membership_committee,
        "profile_country": profile_country,
        "social_profile_urls": [
            {
                "platform": platform_key,
                "urls": [url for url in platform_urls if url.strip()],
            }
            for platform_key, platform_urls in social_urls_by_platform.items()
            if platform_urls
        ],
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
        "social_profiles": social_profiles,
        "website_urls": website_urls,
        "website_url_values": [url.strip() for url in website_url_values if url.strip()],
        "rss_urls": rss_urls,
        "rss_url_values": [url.strip() for url in _as_list(_data_get(data, "fasRssUrl", [])) if url.strip()],
        "rhbz_email": _first(data, "fasRHBZEmail", "") or "",
        "github_username": _first(data, "fasGitHubUsername", "") or "",
        "gitlab_username": _first(data, "fasGitLabUsername", "") or "",
        "gpg_keys": gpg_keys,
        "ssh_keys": ssh_keys,
    }


def _build_user_profile_summary_bootstrap(context: dict[str, object]) -> dict[str, object]:
    fu = cast(FreeIPAUser, context["fu"])
    avatar_url_by_username, _avatar_resolution_count, _avatar_fallback_count = resolve_avatar_urls_for_users(
        [fu],
        width=220,
        height=220,
    )

    current_time = cast(object | None, context["current_time"])
    current_time_label = current_time.strftime("%A %H:%M:%S") if current_time is not None else ""

    return {
        "fullName": fu.get_full_name(),
        "username": fu.username,
        "email": str(context["profile_email"]),
        "avatarUrl": avatar_url_by_username.get(fu.username, ""),
        "viewerIsMembershipCommittee": bool(context["viewer_is_membership_committee"]),
        "profileCountry": str(context["profile_country"]) if bool(context["viewer_is_membership_committee"]) else "",
        "pronouns": str(context["pronouns"]),
        "locale": str(context["locale"]),
        "timezoneName": str(context["timezone_name"]),
        "currentTimeLabel": current_time_label,
        "ircNicks": cast(list[str], context["irc_nicks"]),
        "socialProfiles": cast(list[dict[str, object]], context["social_profiles"]),
        "websiteUrls": cast(list[dict[str, str | None]], context["website_urls"]),
        "rssUrls": cast(list[dict[str, str | None]], context["rss_urls"]),
        "rhbzEmail": str(context["rhbz_email"]),
        "githubUsername": str(context["github_username"]),
        "gitlabUsername": str(context["gitlab_username"]),
        "gpgKeys": cast(list[str], context["gpg_keys"]),
        "sshKeys": cast(list[str], context["ssh_keys"]),
        "isSelf": bool(context["is_self"]),
    }


def _build_user_profile_summary_data(context: dict[str, object]) -> dict[str, object]:
    summary = _build_user_profile_summary_bootstrap(context)
    summary.pop("currentTimeLabel", None)
    summary.pop("profileCountry", None)
    summary["countryCode"] = str(context["country_code"]) if bool(context["viewer_is_membership_committee"]) else ""
    summary["socialProfiles"] = cast(list[dict[str, object]], context["social_profile_urls"])
    summary["websiteUrls"] = cast(list[str], context["website_url_values"])
    summary["rssUrls"] = cast(list[str], context["rss_url_values"])
    return summary


def _build_user_profile_groups_bootstrap(context: dict[str, object]) -> dict[str, object]:
    fu = cast(FreeIPAUser, context["fu"])
    groups = cast(list[dict[str, str]], context["groups"])
    agreements = cast(list[str], context["agreements"])
    missing_agreements = cast(list[dict[str, object]], context["missing_agreements"])

    return {
        "username": fu.username,
        "groups": [
            {
                "cn": str(group["cn"]),
                "role": str(group["role"]),
            }
            for group in groups
        ],
        "agreements": [str(agreement) for agreement in agreements],
        "missingAgreements": [
            {
                "cn": str(agreement["cn"]),
                "requiredBy": [str(group_cn) for group_cn in cast(list[str], agreement["required_by"])],
            }
            for agreement in missing_agreements
        ],
        "isSelf": bool(context["is_self"]),
    }


def _build_user_profile_groups_data(context: dict[str, object]) -> dict[str, object]:
    fu = cast(FreeIPAUser, context["fu"])
    groups = cast(list[dict[str, str]], context["groups"])
    agreements = cast(list[str], context["agreements"])
    missing_agreements = cast(list[dict[str, object]], context["missing_agreements"])

    return {
        "username": fu.username,
        "groups": [
            {
                "cn": str(group["cn"]),
                "role": "sponsor" if str(group["role"]).strip().lower() == "sponsor" else "member",
            }
            for group in groups
        ],
        "agreements": [str(agreement) for agreement in agreements],
        "missingAgreements": [
            {
                "cn": str(agreement["cn"]),
                "requiredBy": [str(group_cn) for group_cn in cast(list[str], agreement["required_by"])],
            }
            for agreement in missing_agreements
        ],
        "isSelf": bool(context["is_self"]),
    }


def _profile_context_for_request(request: HttpRequest, username: str) -> dict[str, object]:
    username = normalize_freeipa_username(username)
    if not username:
        raise Http404("User not found")

    viewer_username = get_username(request)
    logger.debug("User profile API: username=%s viewer=%s", username, viewer_username)

    fu = _get_full_user(username)
    if not fu:
        raise Http404("User not found")

    resolved_username = normalize_freeipa_username(fu.username)

    return _profile_context_for_user(
        request,
        fu=fu,
        is_self=resolved_username == viewer_username,
        viewer_is_membership_committee=_is_membership_committee_viewer(request),
    )


def _serialize_user_profile_action(action: dict[str, str]) -> dict[str, object]:
    payload: dict[str, str | int] = {
        "id": action["id"],
        "label": action["label"],
        "urlLabel": action["url_label"],
    }
    request_id = action.get("request_id")
    if request_id:
        payload["requestId"] = int(request_id)

    agreement_cn = action.get("agreement_cn")
    if agreement_cn:
        payload["agreementCn"] = agreement_cn

    return payload


def _serialize_user_profile_action_data(action: dict[str, str]) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": action["id"],
    }
    request_id = action.get("request_id")
    if request_id:
        payload["requestId"] = int(request_id)

    agreement_cn = action.get("agreement_cn")
    if agreement_cn:
        payload["agreementCn"] = agreement_cn

    return payload


def _serialize_user_profile_account_setup(context: dict[str, object]) -> dict[str, object]:
    required_actions = cast(list[dict[str, str]], context["account_setup_required_actions"])
    recommended_actions = cast(list[dict[str, str]], context["account_setup_recommended_actions"])

    return {
        "requiredActions": [_serialize_user_profile_action(action) for action in required_actions],
        "requiredIsRfi": bool(context["account_setup_required_is_rfi"]),
        "recommendedActions": [_serialize_user_profile_action(action) for action in recommended_actions],
        "recommendedDismissKey": f"astra:profile-recommended-dismissed:{cast(FreeIPAUser, context['fu']).username}",
    }


def _serialize_user_profile_account_setup_data(context: dict[str, object]) -> dict[str, object]:
    required_actions = cast(list[dict[str, str]], context["account_setup_required_actions"])
    recommended_actions = cast(list[dict[str, str]], context["account_setup_recommended_actions"])

    return {
        "requiredActions": [_serialize_user_profile_action_data(action) for action in required_actions],
        "requiredIsRfi": bool(context["account_setup_required_is_rfi"]),
        "recommendedActions": [_serialize_user_profile_action_data(action) for action in recommended_actions],
        "recommendedDismissKey": f"astra:profile-recommended-dismissed:{cast(FreeIPAUser, context['fu']).username}",
    }


def _serialize_profile_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _serialize_user_profile_membership_type_data(membership_type: MembershipType | dict[str, object]) -> dict[str, str]:
    if isinstance(membership_type, MembershipType):
        return {
            "name": membership_type.name,
            "code": membership_type.code,
            "description": membership_type.description,
        }
    if isinstance(membership_type, dict):
        return {
            "name": str(membership_type.get("name", "")),
            "code": str(membership_type.get("code", "")),
            "description": str(membership_type.get("description", "")),
        }
    raise TypeError("Expected MembershipType data")


def _format_profile_membership_date(value: object, *, timezone_name: str, fmt: str) -> str:
    if not isinstance(value, datetime):
        return ""

    if timezone_name:
        try:
            value = timezone.localtime(value, timezone=ZoneInfo(timezone_name))
        except Exception:
            value = timezone.localtime(value, timezone=ZoneInfo("UTC"))
    else:
        value = timezone.localtime(value, timezone=ZoneInfo("UTC"))

    return date_format(value, fmt)


def _serialize_user_profile_membership_type(membership_type: MembershipType) -> dict[str, str]:
    return {
        **_serialize_user_profile_membership_type_data(membership_type),
        "className": membership_tier_class(membership_type.code),
    }


def _serialize_user_profile_membership_badge(membership_type: MembershipType) -> dict[str, object]:
    tier_class = membership_tier_class(membership_type.code)
    return {
        "label": membership_type.name,
        "className": f"badge alx-status-badge {tier_class} alx-status-badge--active",
    }


def _serialize_user_profile_pending_badge(status: str, *, is_owner: bool) -> dict[str, object]:
    is_on_hold = status == MembershipRequest.Status.on_hold
    label = "Action required" if is_on_hold and is_owner else "On hold" if is_on_hold else "Under review"
    status_class = "alx-status-badge--action" if is_on_hold else "alx-status-badge--review"
    legacy_class = "membership-action-required" if is_on_hold else "membership-under-review"
    return {
        "label": label,
        "className": f"badge {legacy_class} alx-status-badge {status_class}",
    }


def _serialize_user_profile_membership_entry(
    entry: dict[str, object],
    *,
    index: int,
    timezone_name: str,
    is_owner: bool,
    can_view: bool,
    can_manage: bool,
    username: str,
    csrf_token: str,
    next_url: str,
) -> dict[str, object]:
    membership_type = cast(MembershipType, entry["membership_type"])
    is_expiring_soon = bool(entry["is_expiring_soon"])
    expires_label = _format_profile_membership_date(
        entry["expires_at"],
        timezone_name=timezone_name,
        fmt="M j, Y H:i" if is_expiring_soon else "M j, Y",
    )
    if is_expiring_soon and expires_label:
        expires_label = f"{expires_label} ({timezone_name or 'UTC'})"

    request_id = entry["request_id"]
    management = None
    if can_manage:
        modal_id = f"expiry-modal-{index}"
        input_id = f"expires-on-{index}"
        initial_value = _format_profile_membership_date(entry["expires_at"], timezone_name="UTC", fmt="Y-m-d")
        current_expiration = _format_profile_membership_date(entry["expires_at"], timezone_name="UTC", fmt="M j, Y")
        management = {
            "modalId": modal_id,
            "inputId": input_id,
            "expiryActionUrl": reverse("membership-set-expiry", args=[username, membership_type.code]),
            "terminateActionUrl": reverse("membership-terminate", args=[username, membership_type.code]),
            "csrfToken": csrf_token,
            "nextUrl": next_url,
            "initialValue": initial_value,
            "minValue": _format_profile_membership_date(timezone.now(), timezone_name="UTC", fmt="Y-m-d"),
            "currentText": f"Current expiration: {current_expiration}" if current_expiration else "",
            "terminator": username,
        }

    return {
        "kind": "membership",
        "key": f"membership-{membership_type.code}",
        "requestId": int(request_id) if request_id and (is_owner or can_view) else None,
        "membershipType": _serialize_user_profile_membership_type(membership_type),
        "badge": _serialize_user_profile_membership_badge(membership_type),
        "memberSinceLabel": _format_profile_membership_date(
            entry["created_at"],
            timezone_name=timezone_name,
            fmt="F Y",
        ),
        "expiresLabel": expires_label if is_owner or can_view else "",
        "expiresTone": "danger" if is_expiring_soon else "muted",
        "canRenew": bool(is_owner and is_expiring_soon and not bool(entry["has_pending_request_in_category"])),
        "canRequestTierChange": bool(is_owner and bool(entry["can_request_tier_change"])),
        "management": management,
    }


def _serialize_user_profile_pending_membership_entry(
    entry: dict[str, object],
    *,
    is_owner: bool,
    can_view: bool,
) -> dict[str, object]:
    membership_type = cast(MembershipType, entry["membership_type"])
    request_id = int(entry["request_id"])
    status = str(entry["status"])
    return {
        "kind": "pending",
        "key": f"pending-{request_id}",
        "membershipType": _serialize_user_profile_membership_type(membership_type),
        "requestId": request_id,
        "status": status,
        "organizationName": str(entry["organization_name"]),
        "badge": _serialize_user_profile_pending_badge(
            status,
            is_owner=is_owner,
        ),
    }


def _user_profile_membership_permissions(request: HttpRequest) -> tuple[bool, bool, bool]:
    review_permissions = membership_review_permissions(request.user)
    membership_can_view = bool(review_permissions["membership_can_view"])
    membership_can_write = bool(
        review_permissions["membership_can_add"]
        or review_permissions["membership_can_change"]
        or review_permissions["membership_can_delete"]
    )
    membership_can_manage = bool(review_permissions["membership_can_change"] and review_permissions["membership_can_delete"])
    return membership_can_view, membership_can_write, membership_can_manage


def _build_user_profile_membership_notes_bootstrap(
    *,
    request: HttpRequest,
    username: str,
    membership_can_view: bool,
    membership_can_write: bool,
) -> dict[str, object]:
    target_params = urlencode({"target_type": "user", "target": username})
    return {
        "summaryUrl": f"{reverse('api-membership-notes-aggregate-summary')}?{target_params}",
        "detailUrl": f"{reverse('api-membership-notes-aggregate')}?{target_params}",
        "addUrl": reverse("api-membership-notes-aggregate-add"),
        "csrfToken": get_token(request),
        "nextUrl": request.get_full_path(),
        "canView": membership_can_view,
        "canWrite": membership_can_write,
        "targetType": "user",
        "target": username,
    }


def _serialize_user_profile_membership(context: dict[str, object], request: HttpRequest) -> dict[str, object]:
    fu = cast(FreeIPAUser, context["fu"])
    membership_can_view, membership_can_write, membership_can_manage = _user_profile_membership_permissions(request)
    is_owner = bool(context["is_self"])
    timezone_name = str(context["timezone_name"])
    membership_entries = cast(list[dict[str, object]], context["memberships"])
    pending_entries = cast(list[dict[str, object]], context["membership_pending_requests"])
    csrf_token = get_token(request)

    notes = None
    if membership_can_view:
        notes = _build_user_profile_membership_notes_bootstrap(
            request=request,
            username=fu.username,
            membership_can_view=membership_can_view,
            membership_can_write=membership_can_write,
        )

    visible_pending_entries = pending_entries if is_owner or membership_can_view else []
    return {
        "showCard": bool(context["show_membership_card"]),
        "username": fu.username,
        "canViewHistory": membership_can_view,
        "canRequestAny": bool(context["membership_can_request_any"]),
        "isOwner": is_owner,
        "entries": [
            _serialize_user_profile_membership_entry(
                entry,
                index=index,
                timezone_name=timezone_name,
                is_owner=is_owner,
                can_view=membership_can_view,
                can_manage=membership_can_manage,
                username=fu.username,
                csrf_token=csrf_token,
                next_url=request.get_full_path(),
            )
            for index, entry in enumerate(membership_entries, start=1)
        ],
        "pendingEntries": [
            _serialize_user_profile_pending_membership_entry(entry, is_owner=is_owner, can_view=membership_can_view)
            for entry in visible_pending_entries
        ],
        "notes": notes,
    }


def _serialize_user_profile_membership_entry_data(
    entry: dict[str, object],
    *,
    is_owner: bool,
    can_view: bool,
    can_manage: bool,
) -> dict[str, object]:
    membership_type = cast(MembershipType | dict[str, object], entry["membership_type"])
    membership_type_data = _serialize_user_profile_membership_type_data(membership_type)
    request_id = entry["request_id"]

    return {
        "kind": "membership",
        "key": f"membership-{membership_type_data['code']}",
        "requestId": int(request_id) if request_id and (is_owner or can_view) else None,
        "membershipType": membership_type_data,
        "createdAt": _serialize_profile_datetime(entry.get("created_at")),
        "expiresAt": _serialize_profile_datetime(entry.get("expires_at")),
        "isExpiringSoon": bool(entry["is_expiring_soon"]),
        "canRenew": bool(is_owner and bool(entry["is_expiring_soon"]) and not bool(entry["has_pending_request_in_category"])),
        "canRequestTierChange": bool(is_owner and bool(entry["can_request_tier_change"])),
        "canManage": can_manage,
    }


def _serialize_user_profile_pending_membership_entry_data(
    entry: dict[str, object],
    *,
    is_owner: bool,
    can_view: bool,
) -> dict[str, object]:
    membership_type = cast(MembershipType | dict[str, object], entry["membership_type"])
    request_id = int(entry["request_id"])
    status = str(entry["status"])
    return {
        "kind": "pending",
        "key": f"pending-{request_id}",
        "membershipType": _serialize_user_profile_membership_type_data(membership_type),
        "requestId": request_id,
        "status": status,
        "organizationName": str(entry["organization_name"]),
    }


def _serialize_user_profile_membership_data(context: dict[str, object], request: HttpRequest) -> dict[str, object]:
    fu = cast(FreeIPAUser, context["fu"])
    membership_can_view, _membership_can_write, membership_can_manage = _user_profile_membership_permissions(request)
    is_owner = bool(context["is_self"])
    membership_entries = cast(list[dict[str, object]], context["memberships"])
    pending_entries = cast(list[dict[str, object]], context["membership_pending_requests"])

    visible_pending_entries = pending_entries if is_owner or membership_can_view else []
    return {
        "showCard": bool(context["show_membership_card"]),
        "username": fu.username,
        "canViewHistory": membership_can_view,
        "canRequestAny": bool(context["membership_can_request_any"]),
        "isOwner": is_owner,
        "entries": [
            _serialize_user_profile_membership_entry_data(
                entry,
                is_owner=is_owner,
                can_view=membership_can_view,
                can_manage=membership_can_manage,
            )
            for entry in membership_entries
        ],
        "pendingEntries": [
            _serialize_user_profile_pending_membership_entry_data(entry, is_owner=is_owner, can_view=membership_can_view)
            for entry in visible_pending_entries
        ],
    }


def _build_user_profile_payload(context: dict[str, object], request: HttpRequest) -> dict[str, object]:
    return {
        "summary": _build_user_profile_summary_bootstrap(context),
        "groups": _build_user_profile_groups_bootstrap(context),
        "membership": _serialize_user_profile_membership(context, request),
        "accountSetup": _serialize_user_profile_account_setup(context),
    }


def _build_user_profile_detail_payload(context: dict[str, object], request: HttpRequest) -> dict[str, object]:
    return {
        "summary": _build_user_profile_summary_data(context),
        "groups": _build_user_profile_groups_data(context),
        "membership": _serialize_user_profile_membership_data(context, request),
        "accountSetup": _serialize_user_profile_account_setup_data(context),
    }


def home(request: HttpRequest) -> HttpResponse:
    username = get_username(request)
    if not username:
        messages.error(request, "Unable to determine your username.")
        return redirect("login")
    return redirect("user-profile", username=username)


def user_profile(request: HttpRequest, username: str) -> HttpResponse:
    username = normalize_freeipa_username(username)
    if not username:
        raise Http404("User not found")

    viewer_username = get_username(request)
    logger.debug("User profile shell view: username=%s viewer=%s", username, viewer_username)
    membership_can_view, membership_can_write, _membership_can_manage = _user_profile_membership_permissions(request)
    return render(
        request,
        "core/user_profile.html",
        {
            "profile_username": username,
            "settings_profile_url": settings_url(tab="profile"),
            "membership_history_url_template": f"{reverse('membership-audit-log-user', args=['__username__'])}?{urlencode({'username': '__username__'})}",
            "membership_request_url": reverse("membership-request"),
            "membership_request_detail_url_template": reverse("membership-request-detail", args=[123456789]).replace(
                "123456789", "__request_id__"
            ),
            "membership_set_expiry_url_template": reverse(
                "membership-set-expiry", args=["__username__", "__membership_type_code__"]
            ),
            "membership_terminate_url_template": reverse(
                "membership-terminate", args=["__username__", "__membership_type_code__"]
            ),
            "group_detail_url_template": reverse("group-detail", args=["__group_name__"]),
            "agreements_url_template": f"{settings_url(tab='agreements')}&agreement=__agreement_cn__",
            "settings_country_code_url": settings_url(tab="profile", highlight="country_code"),
            "settings_emails_url": settings_url(tab="emails"),
            "csrf_token": get_token(request),
            "next_url": request.get_full_path(),
            "membership_notes_summary_url": f"{reverse('api-membership-notes-aggregate-summary')}?{urlencode({'target_type': 'user', 'target': username})}",
            "membership_notes_detail_url": f"{reverse('api-membership-notes-aggregate')}?{urlencode({'target_type': 'user', 'target': username})}",
            "membership_notes_add_url": reverse("api-membership-notes-aggregate-add"),
            "membership_notes_can_view": membership_can_view,
            "membership_notes_can_write": membership_can_write,
        },
    )


def user_profile_api(request: HttpRequest, username: str) -> JsonResponse:
    context = _profile_context_for_request(request, username)
    return JsonResponse(_build_user_profile_payload(context, request))


@require_GET
def user_profile_detail_api(request: HttpRequest, username: str) -> JsonResponse:
    context = _profile_context_for_request(request, username)
    return JsonResponse(_build_user_profile_detail_payload(context, request))


def users(request: HttpRequest) -> HttpResponse:
    if not can_view_user_directory(request.user):
        raise Http404

    q = _normalize_str(request.GET.get("q"))

    return render(
        request,
        "core/users.html",
        {
            "q": q,
        },
    )


def users_api(request: HttpRequest) -> JsonResponse:
    if not can_view_user_directory(request.user):
        raise Http404

    q, page_number, _base_query, _page_url_prefix = parse_grid_query(request)
    users_list = snapshot_freeipa_users()

    users_page, paginator, page_obj, page_numbers, show_first, show_last = build_user_grid_page(
        users_list=users_list,
        q=q,
        page_number=page_number,
        per_page=28,
    )

    users_page_list = list(users_page)
    avatar_url_by_username, avatar_resolution_count, avatar_fallback_count = resolve_avatar_urls_for_users(
        users_page_list,
        width=50,
        height=50,
    )

    items: list[dict[str, str]] = []
    for user in users_page_list:
        username = try_get_username_from_user(user)
        if not username:
            continue
        user_avatar_url = avatar_url_by_username.get(username, "")
        items.append(
            {
                "username": username,
                "full_name": try_get_full_name(user),
                "avatar_url": user_avatar_url,
            }
        )

    logger.info(
        "users_api_metrics route=/api/v1/users page_size=%s avatar_resolution_count=%s avatar_fallback_count=%s",
        len(items),
        avatar_resolution_count,
        avatar_fallback_count,
    )

    payload = {
        "users": items,
        "pagination": serialize_pagination(
            {
                "paginator": paginator,
                "page_obj": page_obj,
                "page_numbers": page_numbers,
                "show_first": show_first,
                "show_last": show_last,
            }
        ),
    }
    return JsonResponse(payload)
