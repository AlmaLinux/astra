import logging
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from avatar.templatetags.avatar_tags import avatar_url
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.agreements import (
    has_enabled_agreements,
    list_agreements_for_user,
    missing_required_agreements_for_user_in_group,
)
from core.country_codes import country_code_status_from_user_data, country_name_from_code
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
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
from core.models import MembershipRequest
from core.permissions import can_view_user_directory
from core.templatetags._grid_tag_utils import parse_grid_query
from core.templatetags._user_helpers import try_get_full_name
from core.templatetags.core_user_grid import build_user_grid_page
from core.views_utils import (
    _normalize_str,
    agreement_settings_url,
    get_username,
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
                    "label": "We're having trouble delivering your emails: your address may have bounced or been marked as spam",
                    "url": settings_url(tab="emails"),
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
                    "url": reverse("membership-request-detail", args=[request_id]),
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
                    "url": reverse("membership-request-detail", args=[request_id]),
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

    # Test helpers and some stubs use user-like objects without this attribute.
    profile_is_private = fu.fas_is_private if hasattr(fu, "fas_is_private") else False
    show_membership_card = (not profile_is_private) or is_self or viewer_is_membership_committee

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
        "show_membership_card": show_membership_card,
        "viewer_is_membership_committee": viewer_is_membership_committee,
        "profile_country": profile_country,
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
    viewer_is_membership_committee = _is_membership_committee_viewer(request)

    context = _profile_context_for_user(
        request,
        fu=fu,
        is_self=is_self,
        viewer_is_membership_committee=viewer_is_membership_committee,
    )
    return render(request, "core/user_profile.html", context)


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


def users_grid(request: HttpRequest) -> JsonResponse:
    if not can_view_user_directory(request.user):
        raise Http404

    q, page_number, _base_query, page_url_prefix = parse_grid_query(request)
    users_list = FreeIPAUser.all()

    users_page, paginator, page_obj, page_numbers, show_first, show_last = build_user_grid_page(
        users_list=users_list,
        q=q,
        page_number=page_number,
        per_page=28,
    )

    items: list[dict[str, str]] = []
    for user in users_page:
        username = try_get_username_from_user(user)
        if not username:
            continue
        try:
            user_avatar_url = str(avatar_url(user, 50, 50) or "").strip()
        except Exception:
            user_avatar_url = ""
        items.append(
            {
                "username": username,
                "full_name": try_get_full_name(user),
                "avatar_url": user_avatar_url,
            }
        )

    page_url_prefix = f"{reverse('users')}{page_url_prefix}"
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    payload = {
        "users": items,
        "empty_label": "No users found.",
        "pagination": {
            "count": paginator.count,
            "page": page_obj.number,
            "num_pages": paginator.num_pages,
            "page_numbers": page_numbers,
            "show_first": show_first,
            "show_last": show_last,
            "has_previous": page_obj.has_previous(),
            "has_next": page_obj.has_next(),
            "previous_page_number": page_obj.previous_page_number() if page_obj.has_previous() else None,
            "next_page_number": page_obj.next_page_number() if page_obj.has_next() else None,
            "start_index": start_index,
            "end_index": end_index,
            "page_url_prefix": page_url_prefix,
        },
    }
    return JsonResponse(payload)
