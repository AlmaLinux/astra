from collections.abc import Callable, Mapping, Sequence

from django.db.models import Prefetch, Q, QuerySet
from django.templatetags.static import static
from django.utils.formats import date_format
from django.utils.timezone import localtime

from core.avatar_providers import resolve_avatar_urls_for_users
from core.freeipa.user import FreeIPAUser
from core.membership import visible_committee_membership_requests
from core.membership_constants import MembershipCategoryCode
from core.membership_notes import CUSTOS, last_votes
from core.models import Membership, MembershipLog, MembershipRequest, Note
from core.templatetags.core_membership_notes import (
    _avatar_users_by_username,
    _email_id_from_action,
    _email_modals_for_notes,
    _group_timeline_entries,
    _normalize_response_snapshot,
    _request_resubmitted_new_snapshots_by_note_id,
    _timeline_entries_for_notes,
)
from core.templatetags.core_membership_responses import membership_response_value
from core.views_utils import _normalize_str


def resolve_requested_by(
    username: str,
    *,
    users_by_username: Mapping[str, FreeIPAUser] | None = None,
) -> tuple[str, bool]:
    normalized_username = _normalize_str(username).lower()
    if not normalized_username:
        return "", False
    if users_by_username is not None:
        user = users_by_username.get(normalized_username)
    else:
        user = FreeIPAUser.get(normalized_username)
    if user is None:
        return "", True
    return user.full_name, False


def _membership_request_base_queryset() -> QuerySet[MembershipRequest]:
    return MembershipRequest.objects.select_related("membership_type", "requested_organization").prefetch_related(
        Prefetch(
            "logs",
            queryset=MembershipLog.objects.filter(action=MembershipLog.Action.requested)
            .only("actor_username", "membership_request_id", "created_at")
            .order_by("created_at", "pk"),
            to_attr="requested_logs",
        )
    )


def _membership_request_section_queryset(
    *,
    status: str,
    ordering: tuple[str, ...],
) -> QuerySet[MembershipRequest]:
    return _membership_request_base_queryset().filter(status=status).order_by(*ordering)


def _load_membership_request_section(
    *,
    status: str,
    ordering: tuple[str, ...],
) -> list[MembershipRequest]:
    return list(_membership_request_section_queryset(status=status, ordering=ordering))


def _build_lookup_usernames(
    requests: Sequence[MembershipRequest],
    *,
    include_requested_by: bool,
) -> set[str]:
    lookup_usernames: set[str] = set()
    for membership_request in requests:
        normalized_requested_username = _normalize_str(membership_request.requested_username).lower()
        if membership_request.is_user_target and normalized_requested_username:
            lookup_usernames.add(normalized_requested_username)

        if not include_requested_by:
            continue

        requested_log = membership_request.requested_logs[0] if membership_request.requested_logs else None
        requested_by_username = _normalize_str(requested_log.actor_username).lower() if requested_log is not None else ""
        if requested_by_username:
            lookup_usernames.add(requested_by_username)

    return lookup_usernames


def _build_shell_lookup_usernames(*, querysets: Sequence[QuerySet[MembershipRequest]]) -> set[str]:
    lookup_usernames: set[str] = set()
    for queryset in querysets:
        for username in queryset.values_list("requested_username", flat=True):
            normalized_username = _normalize_str(username).lower()
            if normalized_username:
                lookup_usernames.add(normalized_username)

    return lookup_usernames


def _visible_membership_request_queryset(
    queryset: QuerySet[MembershipRequest],
    *,
    live_usernames: Sequence[str],
) -> QuerySet[MembershipRequest]:
    return queryset.filter(Q(requested_organization__isnull=False) | Q(requested_username__in=live_usernames))


def _build_membership_request_rows(
    *,
    requests: list[MembershipRequest],
    users_by_username: Mapping[str, FreeIPAUser],
    visible_membership_requests: Callable[..., list[MembershipRequest]],
    resolve_requested_by_func: Callable[..., tuple[str, bool]],
    include_rows: bool,
) -> tuple[list[MembershipRequest], list[dict[str, object]]]:
    visible_requests = visible_membership_requests(
        requests,
        live_usernames=users_by_username.keys(),
    )
    if not include_rows:
        return visible_requests, []

    rows: list[dict[str, object]] = []
    for membership_request in visible_requests:
        requested_log = membership_request.requested_logs[0] if membership_request.requested_logs else None
        requested_by_username = requested_log.actor_username if requested_log is not None else ""
        requested_by_full_name, requested_by_deleted = resolve_requested_by_func(
            requested_by_username,
            users_by_username=users_by_username,
        )

        if membership_request.is_organization_target:
            rows.append(
                {
                    "r": membership_request,
                    "organization": membership_request.requested_organization,
                    "requested_by_username": requested_by_username,
                    "requested_by_full_name": requested_by_full_name,
                    "requested_by_deleted": requested_by_deleted,
                }
            )
            continue

        normalized_requested_username = _normalize_str(membership_request.requested_username).lower()
        freeipa_user = users_by_username.get(normalized_requested_username)
        if freeipa_user is None:
            continue

        rows.append(
            {
                "r": membership_request,
                "full_name": freeipa_user.full_name,
                "requested_by_username": requested_by_username,
                "requested_by_full_name": requested_by_full_name,
                "requested_by_deleted": requested_by_deleted,
            }
        )

    return visible_requests, rows


def _build_is_renewal_by_id(requests: Sequence[MembershipRequest]) -> dict[int, bool]:
    active_user_memberships, active_org_memberships = _load_active_membership_target_sets(
        target_usernames={
            membership_request.requested_username
            for membership_request in requests
            if membership_request.is_user_target and membership_request.requested_username
        },
        target_organization_ids={
            membership_request.requested_organization_id
            for membership_request in requests
            if membership_request.is_organization_target and membership_request.requested_organization_id is not None
        },
        membership_type_ids={membership_request.membership_type_id for membership_request in requests if membership_request.membership_type_id},
    )

    is_renewal_by_id: dict[int, bool] = {}
    for membership_request in requests:
        if membership_request.is_user_target:
            is_renewal_by_id[membership_request.pk] = (
                membership_request.requested_username,
                membership_request.membership_type_id,
            ) in active_user_memberships
            continue

        organization_id = membership_request.requested_organization_id
        is_renewal_by_id[membership_request.pk] = bool(
            organization_id and (organization_id, membership_request.membership_type_id) in active_org_memberships
        )

    return is_renewal_by_id


def _load_active_membership_target_sets(
    *,
    target_usernames: set[str],
    target_organization_ids: set[int],
    membership_type_ids: set[str],
) -> tuple[set[tuple[str, str]], set[tuple[int, str]]]:
    active_user_memberships: set[tuple[str, str]] = set()
    active_org_memberships: set[tuple[int, str]] = set()

    if membership_type_ids and (target_usernames or target_organization_ids):
        active_memberships = Membership.objects.active().filter(membership_type_id__in=membership_type_ids)
        if target_usernames and target_organization_ids:
            active_memberships = active_memberships.filter(
                Q(target_username__in=target_usernames) | Q(target_organization_id__in=target_organization_ids)
            )
        elif target_usernames:
            active_memberships = active_memberships.filter(target_username__in=target_usernames)
        else:
            active_memberships = active_memberships.filter(target_organization_id__in=target_organization_ids)

        for membership in active_memberships.only("target_username", "target_organization_id", "membership_type_id"):
            if membership.target_username:
                active_user_memberships.add((membership.target_username, membership.membership_type_id))
            elif membership.target_organization_id is not None:
                active_org_memberships.add((membership.target_organization_id, membership.membership_type_id))

    return active_user_memberships, active_org_memberships


def _build_filter_options(*, filter_counts: Mapping[str, int]) -> list[dict[str, object]]:
    return [
        {"value": "all", "label": "All", "count": filter_counts["all"]},
        {"value": "renewals", "label": "Renewals", "count": filter_counts["renewals"]},
        {"value": "sponsorships", "label": "Sponsorships", "count": filter_counts["sponsorships"]},
        {"value": "individuals", "label": "Individuals", "count": filter_counts["individuals"]},
        {"value": "mirrors", "label": "Mirrors", "count": filter_counts["mirrors"]},
    ]


def _build_pending_filter_summary(
    *,
    pending_entries: Sequence[tuple[int, str, bool]],
    selected_filter: str,
) -> dict[str, object]:
    filter_counts = {
        "all": len(pending_entries),
        "renewals": 0,
        "sponsorships": 0,
        "individuals": 0,
        "mirrors": 0,
    }
    selected_request_ids: set[int] = set()

    for request_id, category_id, is_renewal in pending_entries:
        if is_renewal:
            filter_counts["renewals"] += 1
        if category_id == MembershipCategoryCode.individual:
            filter_counts["individuals"] += 1
        elif category_id == MembershipCategoryCode.mirror:
            filter_counts["mirrors"] += 1
        elif category_id == MembershipCategoryCode.sponsorship:
            filter_counts["sponsorships"] += 1

        if selected_filter == "all":
            selected_request_ids.add(request_id)
        elif selected_filter == "renewals" and is_renewal:
            selected_request_ids.add(request_id)
        elif selected_filter in {"individuals", "mirrors", "sponsorships"} and category_id == selected_filter.rstrip("s"):
            selected_request_ids.add(request_id)

    if selected_filter == "individuals":
        selected_count = filter_counts["individuals"]
    elif selected_filter == "mirrors":
        selected_count = filter_counts["mirrors"]
    elif selected_filter == "sponsorships":
        selected_count = filter_counts["sponsorships"]
    else:
        selected_count = filter_counts[selected_filter]

    return {
        "filter_counts": filter_counts,
        "filter_options": _build_filter_options(filter_counts=filter_counts),
        "filter_empty": selected_filter != "all" and selected_count == 0,
        "selected_request_ids": selected_request_ids,
    }


def build_membership_request_shell_summary(
    *,
    selected_filter: str,
    lookup_users: Callable[[set[str]], Mapping[str, FreeIPAUser]] = FreeIPAUser.find_lightweight_by_usernames,
) -> dict[str, object]:
    pending_queryset = _membership_request_section_queryset(
        status=MembershipRequest.Status.pending,
        ordering=("requested_at",),
    )
    on_hold_queryset = _membership_request_section_queryset(
        status=MembershipRequest.Status.on_hold,
        ordering=("on_hold_at", "requested_at"),
    )
    live_usernames = tuple(
        lookup_users(_build_shell_lookup_usernames(querysets=(pending_queryset, on_hold_queryset))).keys()
    )

    pending_values = list(
        _visible_membership_request_queryset(pending_queryset, live_usernames=live_usernames).values_list(
            "pk",
            "requested_username",
            "requested_organization_id",
            "membership_type_id",
            "membership_type__category_id",
        )
    )
    active_user_memberships, active_org_memberships = _load_active_membership_target_sets(
        target_usernames={
            str(requested_username)
            for _request_id, requested_username, organization_id, membership_type_id, _category_id in pending_values
            if organization_id is None and requested_username and membership_type_id
        },
        target_organization_ids={
            organization_id
            for _request_id, _requested_username, organization_id, membership_type_id, _category_id in pending_values
            if organization_id is not None and membership_type_id
        },
        membership_type_ids={
            membership_type_id
            for _request_id, _requested_username, _organization_id, membership_type_id, _category_id in pending_values
            if membership_type_id
        },
    )
    pending_entries = [
        (
            request_id,
            str(category_id),
            (str(requested_username), membership_type_id) in active_user_memberships
            if organization_id is None
            else (organization_id, membership_type_id) in active_org_memberships,
        )
        for request_id, requested_username, organization_id, membership_type_id, category_id in pending_values
    ]
    filter_summary = _build_pending_filter_summary(
        pending_entries=pending_entries,
        selected_filter=selected_filter,
    )

    return {
        "pending_count": len(filter_summary["selected_request_ids"]),
        "on_hold_count": _visible_membership_request_queryset(on_hold_queryset, live_usernames=live_usernames).count(),
        "filter_options": filter_summary["filter_options"],
        "filter_empty": filter_summary["filter_empty"],
    }


def build_membership_request_queue_summary(
    *,
    selected_filter: str,
    visible_membership_requests: Callable[..., list[MembershipRequest]] = visible_committee_membership_requests,
    lookup_users: Callable[[set[str]], Mapping[str, FreeIPAUser]] = FreeIPAUser.find_lightweight_by_usernames,
) -> dict[str, object]:
    pending_requests_all = _load_membership_request_section(
        status=MembershipRequest.Status.pending,
        ordering=("requested_at",),
    )
    on_hold_requests_all = _load_membership_request_section(
        status=MembershipRequest.Status.on_hold,
        ordering=("on_hold_at", "requested_at"),
    )
    users_by_username = lookup_users(
        _build_lookup_usernames(pending_requests_all + on_hold_requests_all, include_requested_by=False)
    )
    pending_visible, _pending_rows = _build_membership_request_rows(
        requests=pending_requests_all,
        users_by_username=users_by_username,
        visible_membership_requests=visible_membership_requests,
        resolve_requested_by_func=resolve_requested_by,
        include_rows=False,
    )
    on_hold_visible, _on_hold_rows = _build_membership_request_rows(
        requests=on_hold_requests_all,
        users_by_username=users_by_username,
        visible_membership_requests=visible_membership_requests,
        resolve_requested_by_func=resolve_requested_by,
        include_rows=False,
    )

    category_filters = {
        "individuals": MembershipCategoryCode.individual,
        "mirrors": MembershipCategoryCode.mirror,
        "sponsorships": MembershipCategoryCode.sponsorship,
    }
    is_renewal_by_id = _build_is_renewal_by_id(pending_visible)

    filter_counts = {
        "all": len(pending_visible),
        "renewals": 0,
        "sponsorships": 0,
        "individuals": 0,
        "mirrors": 0,
    }
    for membership_request in pending_visible:
        if is_renewal_by_id.get(membership_request.pk, False):
            filter_counts["renewals"] += 1

        category_id = membership_request.membership_type.category_id
        if category_id == MembershipCategoryCode.individual:
            filter_counts["individuals"] += 1
        elif category_id == MembershipCategoryCode.mirror:
            filter_counts["mirrors"] += 1
        elif category_id == MembershipCategoryCode.sponsorship:
            filter_counts["sponsorships"] += 1

    if selected_filter == "all":
        pending_requests = pending_visible
    else:
        if selected_filter == "renewals":
            match_ids = {membership_request.pk for membership_request in pending_visible if is_renewal_by_id.get(membership_request.pk, False)}
        else:
            category = category_filters.get(selected_filter)
            match_ids = {
                membership_request.pk
                for membership_request in pending_visible
                if membership_request.membership_type.category_id == category
            }
        pending_requests = [membership_request for membership_request in pending_visible if membership_request.pk in match_ids]

    filter_options = _build_filter_options(filter_counts=filter_counts)

    return {
        "pending_requests": pending_requests,
        "on_hold_requests": on_hold_visible,
        "filter_counts": filter_counts,
        "filter_options": filter_options,
        "filter_empty": selected_filter != "all" and not pending_requests,
    }


def build_pending_membership_request_queue(
    *,
    selected_filter: str,
    visible_membership_requests: Callable[..., list[MembershipRequest]] = visible_committee_membership_requests,
    resolve_requested_by_func: Callable[..., tuple[str, bool]] = resolve_requested_by,
    lookup_users: Callable[[set[str]], Mapping[str, FreeIPAUser]] = FreeIPAUser.find_lightweight_by_usernames,
    include_rows: bool = True,
) -> dict[str, object]:
    pending_requests_all = _load_membership_request_section(
        status=MembershipRequest.Status.pending,
        ordering=("requested_at",),
    )
    users_by_username = lookup_users(
        _build_lookup_usernames(pending_requests_all, include_requested_by=include_rows)
    )
    pending_visible, pending_rows_all = _build_membership_request_rows(
        requests=pending_requests_all,
        users_by_username=users_by_username,
        visible_membership_requests=visible_membership_requests,
        resolve_requested_by_func=resolve_requested_by_func,
        include_rows=include_rows,
    )

    category_filters = {
        "individuals": MembershipCategoryCode.individual,
        "mirrors": MembershipCategoryCode.mirror,
        "sponsorships": MembershipCategoryCode.sponsorship,
    }
    is_renewal_by_id = _build_is_renewal_by_id(pending_visible)

    for row in pending_rows_all:
        row["is_renewal"] = is_renewal_by_id.get(int(row["r"].pk), False)

    filter_counts = {
        "all": len(pending_visible),
        "renewals": 0,
        "sponsorships": 0,
        "individuals": 0,
        "mirrors": 0,
    }
    for membership_request in pending_visible:
        if is_renewal_by_id.get(membership_request.pk, False):
            filter_counts["renewals"] += 1

        category_id = membership_request.membership_type.category_id
        if category_id == MembershipCategoryCode.individual:
            filter_counts["individuals"] += 1
        elif category_id == MembershipCategoryCode.mirror:
            filter_counts["mirrors"] += 1
        elif category_id == MembershipCategoryCode.sponsorship:
            filter_counts["sponsorships"] += 1

    if selected_filter == "all":
        pending_requests = pending_visible
        pending_rows = pending_rows_all
    else:
        if selected_filter == "renewals":
            match_ids = {membership_request.pk for membership_request in pending_visible if is_renewal_by_id.get(membership_request.pk, False)}
        else:
            category = category_filters.get(selected_filter)
            match_ids = {
                membership_request.pk
                for membership_request in pending_visible
                if membership_request.membership_type.category_id == category
            }
        pending_requests = [membership_request for membership_request in pending_visible if membership_request.pk in match_ids]
        pending_rows = [row for row in pending_rows_all if row["r"].pk in match_ids]

    filter_options = _build_filter_options(filter_counts=filter_counts)

    return {
        "pending_requests": pending_requests,
        "pending_rows": pending_rows,
        "filter_counts": filter_counts,
        "filter_options": filter_options,
        "filter_empty": selected_filter != "all" and not pending_requests,
    }


def build_on_hold_membership_request_queue(
    *,
    visible_membership_requests: Callable[..., list[MembershipRequest]] = visible_committee_membership_requests,
    resolve_requested_by_func: Callable[..., tuple[str, bool]] = resolve_requested_by,
    lookup_users: Callable[[set[str]], Mapping[str, FreeIPAUser]] = FreeIPAUser.find_lightweight_by_usernames,
    include_rows: bool = True,
) -> dict[str, object]:
    on_hold_requests_all = _load_membership_request_section(
        status=MembershipRequest.Status.on_hold,
        ordering=("on_hold_at", "requested_at"),
    )
    users_by_username = lookup_users(
        _build_lookup_usernames(on_hold_requests_all, include_requested_by=include_rows)
    )
    on_hold_visible, on_hold_rows = _build_membership_request_rows(
        requests=on_hold_requests_all,
        users_by_username=users_by_username,
        visible_membership_requests=visible_membership_requests,
        resolve_requested_by_func=resolve_requested_by_func,
        include_rows=include_rows,
    )
    is_renewal_by_id = _build_is_renewal_by_id(on_hold_visible)

    for row in on_hold_rows:
        row["is_renewal"] = is_renewal_by_id.get(int(row["r"].pk), False)

    return {
        "on_hold_requests": on_hold_visible,
        "on_hold_rows": on_hold_rows,
    }


def build_datatables_payload(
    *,
    rows: Sequence[dict[str, object]],
    records_total: int,
    draw: int,
) -> dict[str, object]:
    return {
        "draw": draw,
        "recordsTotal": records_total,
        "recordsFiltered": len(rows),
        "data": [
            serialize_membership_request_row(
                row=row,
            )
            for row in rows
        ],
    }


def serialize_membership_request_row(
    *,
    row: dict[str, object],
) -> dict[str, object]:
    membership_request = row["r"]
    requested_by_username = str(row.get("requested_by_username") or "")
    requested_by_show = bool(
        requested_by_username
        and (
            (membership_request.is_user_target and requested_by_username != membership_request.requested_username)
            or (membership_request.is_organization_target and row.get("organization") is not None)
        )
    )

    return {
        "request_id": membership_request.pk,
        "status": membership_request.status,
        "requested_at": membership_request.requested_at.isoformat() if membership_request.requested_at else "",
        "on_hold_since": build_on_hold_state(membership_request=membership_request),
        "target": build_target_payload(row=row),
        "requested_by": {
            "show": requested_by_show,
            "username": requested_by_username,
            "full_name": str(row.get("requested_by_full_name") or ""),
            "deleted": bool(row.get("requested_by_deleted", False)),
        },
        "membership_type": {
            "id": membership_request.membership_type_id,
            "code": membership_request.membership_type.code,
            "name": membership_request.membership_type.name,
            "category": membership_request.membership_type.category_id,
        },
        "is_renewal": bool(row.get("is_renewal", False)),
        "responses": build_response_items(membership_request=membership_request),
    }


def build_target_payload(*, row: dict[str, object]) -> dict[str, object]:
    membership_request = row["r"]
    if membership_request.is_organization_target:
        organization = row.get("organization")
        organization_name = membership_request.organization_display_name or "(unknown organization)"
        return {
            "kind": "organization",
            "label": organization_name,
            "secondary_label": "",
            "organization_id": organization.pk if organization is not None else None,
            "deleted": organization is None,
        }

    requested_username = str(membership_request.requested_username or "")
    full_name = str(row.get("full_name") or "")
    return {
        "kind": "user",
        "label": full_name or requested_username,
        "secondary_label": requested_username if full_name else "",
        "username": requested_username,
        "deleted": bool(row.get("target_deleted", False)),
    }


def build_on_hold_state(*, membership_request: MembershipRequest) -> str | None:
    if membership_request.on_hold_at is None:
        return None
    return membership_request.on_hold_at.isoformat()


def build_response_items(*, membership_request: MembershipRequest) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for response_row in _normalize_response_snapshot(membership_request.responses):
        for question, value in response_row.items():
            items.append(
                {
                    "question": question,
                    "answer_html": str(membership_response_value(value, question)),
                }
            )
    return items


def build_note_summary(
    *,
    notes: Sequence[Note],
    current_username: str,
    review_permissions: Mapping[str, bool],
) -> dict[str, object] | None:
    can_view = bool(review_permissions.get("membership_can_view", False))
    if not can_view:
        return None

    votes_by_user = last_votes(notes)
    approvals = sum(1 for vote in votes_by_user.values() if vote == "approve")
    disapprovals = sum(1 for vote in votes_by_user.values() if vote == "disapprove")
    current_user_vote = votes_by_user.get(current_username.lower()) if current_username else None

    return {
        "note_count": len(notes),
        "approvals": approvals,
        "disapprovals": disapprovals,
        "current_user_vote": current_user_vote,
    }


def build_note_details(
    *,
    notes: list[Note],
    current_username: str,
    review_permissions: Mapping[str, bool],
    membership_request: MembershipRequest | None = None,
    current_responses_by_request_id: Mapping[int, list[dict[str, str]]] | None = None,
) -> dict[str, object] | None:
    can_view = bool(review_permissions.get("membership_can_view", False))
    if not can_view:
        return None

    include_membership_request_links = membership_request is None

    responses_by_request_id = dict(current_responses_by_request_id or {})
    if membership_request is not None:
        responses_by_request_id[int(membership_request.pk)] = _normalize_response_snapshot(membership_request.responses)
    if not responses_by_request_id:
        request_ids = sorted({int(note.membership_request_id) for note in notes if note.membership_request_id is not None})
        if request_ids:
            for membership_request_id, responses in MembershipRequest.objects.filter(pk__in=request_ids).values_list("pk", "responses"):
                responses_by_request_id[int(membership_request_id)] = _normalize_response_snapshot(responses)

    author_usernames = {
        str(note.username or "").strip().lower()
        for note in notes
        if note.username and note.username != CUSTOS and str(note.username or "").strip()
    }
    if membership_request is None:
        users_by_username = FreeIPAUser.find_lightweight_by_usernames(author_usernames)
    else:
        users_by_username = _avatar_users_by_username(notes)
    avatar_url_by_username, _avatar_resolution_count, _avatar_fallback_count = resolve_avatar_urls_for_users(
        list(users_by_username.values()),
        width=40,
        height=40,
    )

    entries = _timeline_entries_for_notes(
        notes,
        current_username=current_username,
        request_resubmitted_new_snapshots_by_note_id=_request_resubmitted_new_snapshots_by_note_id(
            notes,
            current_responses_by_request_id=responses_by_request_id,
        ),
        avatar_users_by_username=users_by_username,
    )
    groups = _group_timeline_entries(entries)
    contacted_email_by_id = {modal["email_id"]: modal for modal in _email_modals_for_notes(notes)}

    return {
        "groups": [
            serialize_note_group(
                group=group,
                include_membership_request_links=include_membership_request_links,
                avatar_url_by_username=avatar_url_by_username,
                contacted_email_by_id=contacted_email_by_id,
            )
            for group in groups
        ],
    }


def serialize_note_group(
    *,
    group: dict[str, object],
    include_membership_request_links: bool,
    avatar_url_by_username: dict[str, str],
    contacted_email_by_id: dict[int, dict[str, object]],
) -> dict[str, object]:
    header_entry = group["header_entry"]
    note = header_entry["note"]
    username = str(note.username or "")
    normalized_username = username.strip().lower()
    avatar_url = ""
    avatar_kind = "default"
    if header_entry.get("is_custos"):
        avatar_kind = "custos"
        avatar_url = static("core/images/almalinux-logo.svg")
    elif normalized_username and normalized_username in avatar_url_by_username:
        avatar_kind = "user"
        avatar_url = avatar_url_by_username[normalized_username]

    payload = {
        "username": username,
        "display_username": str(header_entry.get("display_username") or username),
        "is_self": bool(group.get("is_self", False)),
        "is_custos": bool(header_entry.get("is_custos", False)),
        "avatar_kind": avatar_kind,
        "avatar_url": avatar_url,
        "timestamp_display": date_format(localtime(note.timestamp), "DATETIME_FORMAT"),
        "entries": [
            serialize_note_entry(entry=entry, contacted_email_by_id=contacted_email_by_id)
            for entry in group.get("entries", [])
        ],
    }
    if include_membership_request_links:
        payload["membership_request_id"] = header_entry.get("membership_request_id")
        payload["membership_request_url"] = str(header_entry.get("membership_request_url") or "")
    return payload


def _serialize_contacted_email_detail(email_modal: dict[str, object]) -> dict[str, object]:
    return {
        "email_id": int(email_modal["email_id"]),
        "from_email": str(email_modal.get("from_email") or ""),
        "to": list(email_modal.get("to") or []),
        "cc": list(email_modal.get("cc") or []),
        "bcc": list(email_modal.get("bcc") or []),
        "reply_to": str(email_modal.get("reply_to") or ""),
        "headers": [list(header) for header in list(email_modal.get("headers") or [])],
        "subject": str(email_modal.get("subject") or ""),
        "html": str(email_modal.get("html") or ""),
        "text": str(email_modal.get("text") or ""),
        "recipient_delivery_summary": str(email_modal.get("recipient_delivery_summary") or ""),
        "recipient_delivery_summary_note": str(email_modal.get("recipient_delivery_summary_note") or ""),
        "logs": [
            {
                "date_display": log["date"].strftime("%Y-%m-%d %H:%M:%S %Z") if log.get("date") else "",
                "status": str(log.get("status") or ""),
                "message": str(log.get("message") or ""),
                "exception_type": str(log.get("exception_type") or ""),
            }
            for log in list(email_modal.get("logs") or [])
        ],
    }


def serialize_note_entry(
    *,
    entry: dict[str, object],
    contacted_email_by_id: dict[int, dict[str, object]],
) -> dict[str, object]:
    note = entry["note"]
    payload: dict[str, object] = {
        "kind": str(entry.get("kind") or "message"),
        "bubble_style": str(entry.get("bubble_style") or ""),
    }
    if payload["kind"] == "action":
        payload["label"] = str(entry.get("label") or "")
        payload["icon"] = str(entry.get("icon") or "fa-bolt")
        payload["request_resubmitted_diff_rows"] = list(entry.get("request_resubmitted_diff_rows") or [])
        payload["note_id"] = note.pk
        email_id = _email_id_from_action(note.action if isinstance(note.action, dict) else None)
        if email_id is not None and email_id in contacted_email_by_id:
            payload["contacted_email"] = _serialize_contacted_email_detail(contacted_email_by_id[email_id])
        return payload

    payload["rendered_html"] = str(entry.get("rendered_content") or "")
    payload["is_self"] = bool(entry.get("is_self", False))
    payload["is_custos"] = bool(entry.get("is_custos", False))
    return payload