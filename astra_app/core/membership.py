import datetime
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings
from django.core.cache import cache
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Organization

logger = logging.getLogger(__name__)


_MEMBERSHIP_REVIEW_BADGE_COUNTS_CACHE_KEY = "membership_review_badge_counts:v1"
_MEMBERSHIP_REVIEW_BADGE_COUNTS_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class MembershipRequestEligibility:
    valid_membership_type_codes: set[str]
    extendable_membership_type_codes: set[str]
    blocked_membership_type_codes: set[str]
    pending_membership_category_ids: set[str]


@dataclass(frozen=True, slots=True)
class PendingRequestContext:
    entries: list[dict[str, object]]
    by_category: dict[str, dict[str, object]]
    category_ids: set[str]


@dataclass(frozen=True, slots=True)
class MembershipRequestabilityContext:
    requestable_codes_by_category: dict[str, set[str]]
    requestable_rows: list[tuple[str, str]]
    membership_can_request_any: bool


class FreeIPACallerMode(StrEnum):
    raise_on_error = "raise_on_error"
    log_and_continue = "log_and_continue"
    best_effort = "best_effort"


class FreeIPAMissingUserPolicy(StrEnum):
    treat_as_error = "treat_as_error"
    treat_as_noop = "treat_as_noop"


class FreeIPAGroupRemovalOutcome(StrEnum):
    noop_blank_input = "noop_blank_input"
    user_not_found = "user_not_found"
    already_not_member = "already_not_member"
    removed = "removed"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class FreeIPARepresentativeSyncJournal:
    targeted_group_cns: tuple[str, ...]
    skipped_group_cns: tuple[str, ...]
    old_removed_group_cns: tuple[str, ...]
    new_added_group_cns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FreeIPARepresentativeSyncResult:
    journal: FreeIPARepresentativeSyncJournal
    failed_group_cns: tuple[str, ...]
    failure_details: dict[str, str]


class FreeIPARepresentativeSyncError(RuntimeError):
    def __init__(self, result: FreeIPARepresentativeSyncResult) -> None:
        self.result = result
        details = ", ".join(f"{group_cn}: {message}" for group_cn, message in result.failure_details.items())
        super().__init__(f"FreeIPA representative group sync failed: {details}")


def membership_target_filter(
    *,
    username: str | None = None,
    organization: Organization | None = None,
) -> dict[str, object] | None:
    if (username is None) == (organization is None):
        raise ValueError("Provide exactly one of username or organization.")

    if username is not None:
        normalized_username = str(username or "").strip()
        if not normalized_username:
            return None
        return {"target_username": normalized_username}

    return {"target_organization": organization}


def _normalized_group_cns(group_cns: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    targeted_group_cns: list[str] = []
    skipped_group_cns: list[str] = []
    seen: set[str] = set()

    for raw_group_cn in group_cns:
        group_cn = str(raw_group_cn or "").strip()
        if not group_cn:
            skipped_group_cns.append(group_cn)
            continue
        if group_cn in seen:
            continue
        seen.add(group_cn)
        targeted_group_cns.append(group_cn)

    return tuple(targeted_group_cns), tuple(skipped_group_cns)


def _should_raise_for_failure(caller_mode: FreeIPACallerMode) -> bool:
    return caller_mode == FreeIPACallerMode.raise_on_error


def compute_membership_requestability_context(
    *,
    username: str | None = None,
    organization: Organization | None = None,
    eligibility: MembershipRequestEligibility | None = None,
    held_category_ids: set[str] | None = None,
) -> MembershipRequestabilityContext:
    target_filter = membership_target_filter(username=username, organization=organization)
    if target_filter is None:
        return MembershipRequestabilityContext(
            requestable_codes_by_category={},
            requestable_rows=[],
            membership_can_request_any=False,
        )

    resolved_eligibility = (
        eligibility
        if eligibility is not None
        else get_membership_request_eligibility(username=username, organization=organization)
    )
    requestable_rows = [
        (str(category_id), str(code))
        for category_id, code in requestable_membership_types_for_target(
            username=username,
            organization=organization,
            eligibility=resolved_eligibility,
        ).values_list("category_id", "code")
        if str(category_id or "").strip() and str(code or "").strip()
    ]

    requestable_codes_by_category: dict[str, set[str]] = {}
    for category_id, code in requestable_rows:
        requestable_codes_by_category.setdefault(category_id, set()).add(code)

    resolved_held_category_ids = (
        held_category_ids
        if held_category_ids is not None
        else {
            membership.membership_type.category_id
            for membership in get_valid_memberships(username=username, organization=organization)
        }
    )

    membership_can_request_any = membership_request_can_request_any(
        username=username,
        organization=organization,
        eligibility=resolved_eligibility,
    )
    if membership_can_request_any:
        membership_can_request_any = any(
            category_id not in resolved_held_category_ids
            for category_id, _code in requestable_rows
        )

    return MembershipRequestabilityContext(
        requestable_codes_by_category=requestable_codes_by_category,
        requestable_rows=requestable_rows,
        membership_can_request_any=membership_can_request_any,
    )


def sync_organization_representative_groups(
    *,
    old_representative: str,
    new_representative: str,
    group_cns: tuple[str, ...],
    caller_mode: FreeIPACallerMode,
    missing_user_policy: FreeIPAMissingUserPolicy,
) -> FreeIPARepresentativeSyncResult:
    normalized_old = str(old_representative or "").strip()
    normalized_new = str(new_representative or "").strip()
    targeted_group_cns, skipped_group_cns = _normalized_group_cns(group_cns)

    old_user = FreeIPAUser.get(normalized_old) if normalized_old else None
    new_user = FreeIPAUser.get(normalized_new) if normalized_new else None
    old_groups = set(old_user.groups_list) if old_user is not None else set()
    new_groups = set(new_user.groups_list) if new_user is not None else set()

    old_removed_group_cns: list[str] = []
    new_added_group_cns: list[str] = []
    failed_group_cns: list[str] = []
    failure_details: dict[str, str] = {}

    for group_cn in targeted_group_cns:
        try:
            if normalized_old:
                if old_user is None:
                    if missing_user_policy == FreeIPAMissingUserPolicy.treat_as_error:
                        raise RuntimeError(f"old representative not found in FreeIPA: {normalized_old}")
                elif group_cn in old_groups:
                    old_user.remove_from_group(group_name=group_cn)
                    old_groups.discard(group_cn)
                    old_removed_group_cns.append(group_cn)

            if normalized_new:
                if new_user is None:
                    if missing_user_policy == FreeIPAMissingUserPolicy.treat_as_error:
                        raise RuntimeError(f"new representative not found in FreeIPA: {normalized_new}")
                elif group_cn not in new_groups:
                    new_user.add_to_group(group_name=group_cn)
                    new_groups.add(group_cn)
                    new_added_group_cns.append(group_cn)
        except Exception as exc:
            failed_group_cns.append(group_cn)
            failure_details[group_cn] = str(exc)

    result = FreeIPARepresentativeSyncResult(
        journal=FreeIPARepresentativeSyncJournal(
            targeted_group_cns=targeted_group_cns,
            skipped_group_cns=skipped_group_cns,
            old_removed_group_cns=tuple(old_removed_group_cns),
            new_added_group_cns=tuple(new_added_group_cns),
        ),
        failed_group_cns=tuple(failed_group_cns),
        failure_details=failure_details,
    )

    if failed_group_cns and _should_raise_for_failure(caller_mode):
        raise FreeIPARepresentativeSyncError(result)

    if failed_group_cns and caller_mode == FreeIPACallerMode.log_and_continue:
        logger.warning(
            "sync_organization_representative_groups completed with failures old=%r new=%r failed=%r",
            normalized_old,
            normalized_new,
            failed_group_cns,
        )

    return result


def sync_organization_representative_membership_groups(
    *,
    representative_username: str,
    group_cns: tuple[str, ...],
    old_group_cn_to_remove: str | None,
    membership_request_id: int,
    log_prefix: str,
    caller_mode: FreeIPACallerMode,
    missing_user_policy: FreeIPAMissingUserPolicy,
) -> None:
    """Apply representative-group side effects for an org membership transition.

    This keeps the old-group cleanup and new-group sync semantics aligned
    between the approval workflow and importer without merging their broader
    policy differences.
    """

    normalized_representative = str(representative_username or "").strip()
    normalized_old_group_cn = str(old_group_cn_to_remove or "").strip() or None

    def is_freeipa_noop_error(*, error: Exception, is_add: bool) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        if is_add:
            return "already" in text and "member" in text
        return "not" in text and "member" in text

    if normalized_old_group_cn:
        try:
            old_outcome = remove_organization_representative_from_group_if_present(
                representative_username=normalized_representative,
                group_cn=normalized_old_group_cn,
                caller_mode=caller_mode,
                missing_user_policy=missing_user_policy,
            )
        except Exception as exc:
            if is_freeipa_noop_error(error=exc, is_add=False):
                logger.info(
                    "astra.membership.freeipa_group.not_member group_cn=%r outcome=noop",
                    normalized_old_group_cn,
                    extra={
                        "event": "astra.freeipa.group.mutation",
                        "component": "membership",
                        "outcome": "not_member",
                    },
                )
            else:
                logger.exception(
                    "%s: on_commit old-group cleanup failed request_id=%s org_rep=%r group_cn=%r",
                    log_prefix,
                    membership_request_id,
                    normalized_representative,
                    normalized_old_group_cn,
                    extra=current_exception_log_fields(),
                )
        else:
            if old_outcome == FreeIPAGroupRemovalOutcome.failed:
                logger.error(
                    "%s: on_commit old-group cleanup returned failed request_id=%s org_rep=%r group_cn=%r",
                    log_prefix,
                    membership_request_id,
                    normalized_representative,
                    normalized_old_group_cn,
                )

    try:
        sync_organization_representative_groups(
            old_representative="",
            new_representative=normalized_representative,
            group_cns=group_cns,
            caller_mode=caller_mode,
            missing_user_policy=missing_user_policy,
        )
    except Exception as exc:
        if is_freeipa_noop_error(error=exc, is_add=True) or is_freeipa_noop_error(error=exc, is_add=False):
            noop_outcome = "already_member" if is_freeipa_noop_error(error=exc, is_add=True) else "not_member"
            logger.info(
                "astra.membership.freeipa_group.%s group_cn=%r outcome=noop",
                noop_outcome,
                ",".join(group_cns),
                extra={
                    "event": "astra.freeipa.group.mutation",
                    "component": "membership",
                    "outcome": noop_outcome,
                },
            )
        else:
            logger.exception(
                "%s: on_commit representative sync failed request_id=%s org_rep=%r group_cn=%r",
                log_prefix,
                membership_request_id,
                normalized_representative,
                ",".join(group_cns),
                extra=current_exception_log_fields(),
            )


def rollback_organization_representative_groups(
    *,
    old_representative: str,
    new_representative: str,
    journal: FreeIPARepresentativeSyncJournal,
) -> None:
    normalized_old = str(old_representative or "").strip()
    normalized_new = str(new_representative or "").strip()

    for group_cn in journal.new_added_group_cns:
        if not normalized_new or not group_cn:
            continue
        try:
            new_user = FreeIPAUser.get(normalized_new)
            if new_user is None:
                continue
            new_user.remove_from_group(group_name=group_cn)
        except Exception:
            logger.exception(
                "rollback_organization_representative_groups failed to remove new representative from group new=%r group_cn=%r",
                normalized_new,
                group_cn,
                extra=current_exception_log_fields(),
            )

    for group_cn in journal.old_removed_group_cns:
        if not normalized_old or not group_cn:
            continue
        try:
            old_user = FreeIPAUser.get(normalized_old)
            if old_user is None:
                continue
            old_user.add_to_group(group_name=group_cn)
        except Exception:
            logger.exception(
                "rollback_organization_representative_groups failed to re-add old representative to group old=%r group_cn=%r",
                normalized_old,
                group_cn,
                extra=current_exception_log_fields(),
            )


def remove_organization_representative_from_group_if_present(
    *,
    representative_username: str,
    group_cn: str,
    caller_mode: FreeIPACallerMode,
    missing_user_policy: FreeIPAMissingUserPolicy,
) -> FreeIPAGroupRemovalOutcome:
    normalized_username = str(representative_username or "").strip()
    normalized_group_cn = str(group_cn or "").strip()
    if not normalized_username or not normalized_group_cn:
        return FreeIPAGroupRemovalOutcome.noop_blank_input

    representative = FreeIPAUser.get(normalized_username)
    if representative is None:
        outcome = FreeIPAGroupRemovalOutcome.user_not_found
        if (
            missing_user_policy == FreeIPAMissingUserPolicy.treat_as_error
            and _should_raise_for_failure(caller_mode)
        ):
            raise FreeIPARepresentativeSyncError(
                FreeIPARepresentativeSyncResult(
                    journal=FreeIPARepresentativeSyncJournal(
                        targeted_group_cns=(normalized_group_cn,),
                        skipped_group_cns=(),
                        old_removed_group_cns=(),
                        new_added_group_cns=(),
                    ),
                    failed_group_cns=(normalized_group_cn,),
                    failure_details={
                        normalized_group_cn: f"representative not found in FreeIPA: {normalized_username}",
                    },
                )
            )
        return outcome

    if normalized_group_cn not in representative.groups_list:
        return FreeIPAGroupRemovalOutcome.already_not_member

    try:
        representative.remove_from_group(group_name=normalized_group_cn)
    except Exception as exc:
        if _should_raise_for_failure(caller_mode):
            raise FreeIPARepresentativeSyncError(
                FreeIPARepresentativeSyncResult(
                    journal=FreeIPARepresentativeSyncJournal(
                        targeted_group_cns=(normalized_group_cn,),
                        skipped_group_cns=(),
                        old_removed_group_cns=(),
                        new_added_group_cns=(),
                    ),
                    failed_group_cns=(normalized_group_cn,),
                    failure_details={normalized_group_cn: str(exc)},
                )
            ) from exc
        logger.exception(
            "remove_organization_representative_from_group_if_present failed username=%s group_cn=%s",
            normalized_username,
            normalized_group_cn,
            extra=current_exception_log_fields(),
        )
        return FreeIPAGroupRemovalOutcome.failed

    return FreeIPAGroupRemovalOutcome.removed


def get_valid_memberships(
    *,
    username: str | None = None,
    organization: Organization | None = None,
) -> list[Membership]:
    """Return the current unexpired active memberships for a user or organization."""

    search = membership_target_filter(username=username, organization=organization)
    if search is None:
        return []

    memberships = (
        Membership.objects.select_related("membership_type", "membership_type__category")
        .filter(**search)
        .active()
        .order_by(
            Case(
                When(membership_type__category_id="sponsorship", then=Value(0)),
                When(membership_type__category_id="mirror", then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
            "membership_type__category__sort_order",
            "membership_type__category__name",
            "membership_type__sort_order",
            "membership_type__code",
            "membership_type__pk",
        )
    )

    return list(memberships)


def get_valid_memberships_by_organization_ids(*, organization_ids: Iterable[int]) -> dict[int, list[Membership]]:
    """Return active memberships grouped by target organization for the provided IDs."""

    normalized_org_ids = {int(organization_id) for organization_id in organization_ids}
    if not normalized_org_ids:
        return {}

    memberships = (
        Membership.objects.select_related("membership_type", "membership_type__category")
        .filter(target_organization_id__in=normalized_org_ids)
        .active()
        .order_by(
            "target_organization_id",
            Case(
                When(membership_type__category_id="sponsorship", then=Value(0)),
                When(membership_type__category_id="mirror", then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
            "membership_type__category__sort_order",
            "membership_type__category__name",
            "membership_type__sort_order",
            "membership_type__code",
            "membership_type__pk",
        )
    )

    memberships_by_organization_id: dict[int, list[Membership]] = defaultdict(list)
    for membership in memberships:
        memberships_by_organization_id[membership.target_organization_id].append(membership)

    return {
        organization_id: list(memberships_by_organization_id[organization_id])
        for organization_id in normalized_org_ids
    }


def remove_user_from_group(*, username: str, group_cn: str) -> bool:
    normalized_username = str(username or "").strip()
    normalized_group_cn = str(group_cn or "").strip()
    if not normalized_username or not normalized_group_cn:
        return False

    user = FreeIPAUser.get(normalized_username)
    if user is None:
        logger.warning(
            "remove_user_from_group: user not found username=%s group_cn=%s",
            normalized_username,
            normalized_group_cn,
        )
        return False

    try:
        user.remove_from_group(group_name=normalized_group_cn)
    except Exception:
        logger.exception(
            "remove_user_from_group: failed to remove user from group username=%s group_cn=%s",
            normalized_username,
            normalized_group_cn,
            extra=current_exception_log_fields(),
        )
        return False

    return True


def resolve_request_ids_by_membership_type(
    *,
    username: str | None = None,
    organization: Organization | None = None,
    membership_type_ids: Iterable[str] | None = None,
) -> dict[str, int]:
    """Resolve approved request IDs for each membership type for a user or organization."""

    target_filter = membership_target_filter(username=username, organization=organization)
    if target_filter is None:
        return {}

    if membership_type_ids is None:
        membership_type_ids = Membership.objects.filter(**target_filter).values_list("membership_type_id", flat=True)

    membership_type_id_set = {
        str(membership_type_id or "").strip()
        for membership_type_id in membership_type_ids
        if str(membership_type_id or "").strip()
    }
    if not membership_type_id_set:
        return {}

    log_filters: dict[str, object] = {
        "membership_type_id__in": membership_type_id_set,
        "membership_request__isnull": False,
        "action": MembershipLog.Action.approved,
    }
    log_filters.update(target_filter)

    request_id_by_membership_type_id: dict[str, int] = {}
    logs = (
        MembershipLog.objects.filter(**log_filters)
        .only("membership_type_id", "membership_request_id", "created_at")
        .order_by("-created_at", "-pk")
    )
    for log in logs:
        req_id = log.membership_request_id
        if req_id is None:
            continue
        request_id_by_membership_type_id.setdefault(log.membership_type_id, int(req_id))

    missing = membership_type_id_set - request_id_by_membership_type_id.keys()
    if missing:
        request_filters: dict[str, object] = {
            "membership_type_id__in": missing,
            "status": MembershipRequest.Status.approved,
        }
        if "target_username" in target_filter:
            request_filters["requested_username"] = target_filter["target_username"]
        else:
            request_filters["requested_organization"] = target_filter["target_organization"]

        approved_requests = (
            MembershipRequest.objects.filter(**request_filters)
            .only("pk", "membership_type_id", "decided_at", "requested_at")
            .order_by("-decided_at", "-requested_at", "-pk")
        )
        for req in approved_requests:
            request_id_by_membership_type_id.setdefault(req.membership_type_id, int(req.pk))

    return request_id_by_membership_type_id


def index_pending_requests_by_category(
    pending_requests: Iterable[MembershipRequest],
) -> dict[str, MembershipRequest]:
    """Index pending requests by category with deterministic first-wins semantics.

    Callers must order ``pending_requests`` explicitly for their chosen behavior.
    For example, order newest-first to select the most recent request per category.
    """
    indexed: dict[str, MembershipRequest] = {}
    for pending_request in pending_requests:
        category_id = pending_request.membership_type.category_id
        if category_id not in indexed:
            indexed[category_id] = pending_request
    return indexed


def pending_request_context_entry(
    pending_request: MembershipRequest,
    *,
    is_organization: bool,
) -> dict[str, object]:
    return {
        "membership_type": pending_request.membership_type,
        "requested_at": pending_request.requested_at,
        "pk": pending_request.pk,
        "request_id": pending_request.pk,
        "status": pending_request.status,
        "on_hold_at": pending_request.on_hold_at,
        "is_organization": is_organization,
        "organization_name": (
            pending_request.requested_organization_name
            or (pending_request.requested_organization.name if pending_request.requested_organization else "")
        )
        if is_organization
        else "",
    }


def build_pending_request_context(
    pending_requests: Iterable[MembershipRequest],
    *,
    is_organization: bool,
) -> PendingRequestContext:
    """Build the canonical pending-request display context used by profile-like pages."""

    ordered_pending_requests = list(pending_requests)
    entries = [
        pending_request_context_entry(pending_request, is_organization=is_organization)
        for pending_request in ordered_pending_requests
    ]
    by_category_requests = index_pending_requests_by_category(ordered_pending_requests)
    by_category = {
        category_id: pending_request_context_entry(pending_request, is_organization=is_organization)
        for category_id, pending_request in by_category_requests.items()
    }
    return PendingRequestContext(
        entries=entries,
        by_category=by_category,
        category_ids=set(by_category),
    )


def _normalize_live_usernames(
    live_usernames: Iterable[str] | None,
    *,
    live_users_by_username: Mapping[str, FreeIPAUser] | None = None,
) -> set[str]:
    if live_users_by_username is not None:
        live_usernames = live_users_by_username.keys()
    elif live_usernames is None:
        live_usernames = (user.username for user in FreeIPAUser.all())

    return {
        normalized
        for normalized in (str(username).strip().lower() for username in live_usernames)
        if normalized
    }


def visible_committee_membership_requests(
    pending_requests: Iterable[MembershipRequest],
    *,
    live_usernames: Iterable[str] | None = None,
    live_users_by_username: Mapping[str, FreeIPAUser] | None = None,
) -> list[MembershipRequest]:
    """Return only the pending/on-hold requests the committee can still act on."""

    live_username_set = _normalize_live_usernames(
        live_usernames,
        live_users_by_username=live_users_by_username,
    )
    visible_requests: list[MembershipRequest] = []
    for pending_request in pending_requests:
        if pending_request.is_organization_target:
            if pending_request.requested_organization is None:
                continue
        elif str(pending_request.requested_username or "").strip().lower() not in live_username_set:
            continue

        visible_requests.append(pending_request)

    return visible_requests


def auto_ignore_pending_requests_for_deleted_users() -> int:
    """Auto-ignore pending requests for users that no longer exist in FreeIPA.

    Can be called from cache invalidation handlers or routine operations.
    Returns count of requests that were auto-ignored.
    """
    from core.membership_request_workflow import ignore_membership_request

    try:
        live_usernames = _normalize_live_usernames(None)
    except Exception:
        # If FreeIPA is unavailable, don't auto-ignore anything
        logger.debug("skipped_auto_ignore_freeipa_unavailable")
        return 0

    ignored_count = 0
    pending_all = list(MembershipRequest.objects.filter(status=MembershipRequest.Status.pending))

    for request in pending_all:
        # Only auto-ignore user targets (not org targets).
        if request.is_organization_target:
            continue

        normalized_username = str(request.requested_username or "").strip().lower()
        if not normalized_username or normalized_username in live_usernames:
            continue

        # User doesn't exist in FreeIPA; auto-ignore the request.
        try:
            ignore_membership_request(
                membership_request=request,
                actor_username="system",
            )
            ignored_count += 1
            logger.info(
                "auto_ignored_pending_request_for_deleted_user request_id=%s username=%s",
                request.pk,
                request.requested_username,
                extra={
                    "event": "astra.membership.auto_ignore",
                    "component": "membership",
                    "outcome": "deleted_user",
                },
            )
        except Exception:
            logger.exception(
                "failed_to_auto_ignore_pending_request_for_deleted_user request_id=%s username=%s",
                request.pk,
                request.requested_username,
                extra=current_exception_log_fields(),
            )

    return ignored_count


def invalidate_membership_review_badge_cache() -> None:
    """Invalidate the cached membership review badge counts."""
    try:
        cache.delete(_MEMBERSHIP_REVIEW_BADGE_COUNTS_CACHE_KEY)
    except Exception:
        pass


def get_membership_review_badge_counts() -> dict[str, int]:
    try:
        cached_counts = cache.get(_MEMBERSHIP_REVIEW_BADGE_COUNTS_CACHE_KEY)
    except Exception:
        cached_counts = None

    if isinstance(cached_counts, dict) and {"pending_count", "on_hold_count"}.issubset(cached_counts):
        pending_count = cached_counts["pending_count"]
        on_hold_count = cached_counts["on_hold_count"]
        if isinstance(pending_count, int) and isinstance(on_hold_count, int):
            return {
                "pending_count": pending_count,
                "on_hold_count": on_hold_count,
            }

    # Badge counts are intentionally DB-only so cold, expired, and cache-failure
    # recomputes cannot fall back into request-time FreeIPA fan-out.
    counts = {
        "pending_count": MembershipRequest.objects.filter(status=MembershipRequest.Status.pending).count(),
        "on_hold_count": MembershipRequest.objects.filter(status=MembershipRequest.Status.on_hold).count(),
    }

    try:
        cache.set(
            _MEMBERSHIP_REVIEW_BADGE_COUNTS_CACHE_KEY,
            counts,
            timeout=_MEMBERSHIP_REVIEW_BADGE_COUNTS_TTL_SECONDS,
        )
    except Exception:
        pass

    return counts


def expiring_soon_cutoff(*, now: datetime.datetime | None = None) -> datetime.datetime:
    """Return the timestamp for the expiring-soon cutoff window."""

    reference = now if now is not None else timezone.now()
    return reference + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS)


def get_expiring_memberships(*, days: int = 0) -> list[Membership]:
    """Return all active memberships expiring within the specified days (default 0)."""

    now = timezone.now()
    threshold = now + datetime.timedelta(days=days)

    return list(
        Membership.objects.active()
        .filter(expires_at__lte=threshold, expires_at__gt=now)
        .select_related("membership_type", "membership_type__category", "target_organization")
    )


def get_valid_membership_type_codes(
    *,
    username: str | None = None,
    organization: Organization | None = None,
) -> set[str]:
    """Return active membership type codes for the selected target."""

    return {membership.membership_type_id for membership in get_valid_memberships(username=username, organization=organization)}


def get_extendable_membership_type_codes(
    *,
    username: str | None = None,
    organization: Organization | None = None,
) -> set[str]:
    """Return active membership type codes that are within the expiring-soon window."""

    expiring_soon_by = expiring_soon_cutoff()
    return {
        membership.membership_type_id
        for membership in get_valid_memberships(username=username, organization=organization)
        if membership.expires_at and membership.expires_at <= expiring_soon_by
    }


def get_membership_request_eligibility(
    *,
    username: str | None = None,
    organization: Organization | None = None,
) -> MembershipRequestEligibility:
    """Return request-eligibility sets for a user or organization target."""

    memberships = get_valid_memberships(username=username, organization=organization)
    valid_membership_type_codes = {membership.membership_type_id for membership in memberships}
    expiring_soon_by = expiring_soon_cutoff()
    extendable_membership_type_codes = {
        membership.membership_type_id
        for membership in memberships
        if membership.expires_at is not None and membership.expires_at <= expiring_soon_by
    }
    blocked_membership_type_codes = valid_membership_type_codes - extendable_membership_type_codes

    target_filter = membership_target_filter(username=username, organization=organization)
    if target_filter is None:
        pending_membership_category_ids: set[str] = set()
    else:
        pending_filters: dict[str, object] = {
            "status__in": [MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        }
        if "target_username" in target_filter:
            pending_filters["requested_username"] = target_filter["target_username"]
        else:
            pending_filters["requested_organization"] = target_filter["target_organization"]
        pending_membership_category_ids = set(
            MembershipRequest.objects.filter(**pending_filters).values_list("membership_type__category_id", flat=True)
        )

    return MembershipRequestEligibility(
        valid_membership_type_codes=valid_membership_type_codes,
        extendable_membership_type_codes=extendable_membership_type_codes,
        blocked_membership_type_codes=blocked_membership_type_codes,
        pending_membership_category_ids=pending_membership_category_ids,
    )


def membership_request_can_request_any(
    *,
    username: str | None = None,
    organization: Organization | None = None,
    eligibility: MembershipRequestEligibility | None = None,
) -> bool:
    """Return whether the selected target can submit at least one membership request."""

    return requestable_membership_types_for_target(
        username=username,
        organization=organization,
        eligibility=eligibility,
    ).exists()


def requestable_membership_types_for_target(
    *,
    username: str | None = None,
    organization: Organization | None = None,
    eligibility: MembershipRequestEligibility | None = None,
):
    """Return membership types currently requestable for the selected target."""

    target_filter = membership_target_filter(username=username, organization=organization)
    if target_filter is None:
        return MembershipType.objects.none()

    resolved_eligibility = (
        eligibility
        if eligibility is not None
        else get_membership_request_eligibility(username=username, organization=organization)
    )

    base = MembershipType.objects.filter(enabled=True)
    if "target_username" in target_filter:
        base = base.filter(category__is_individual=True)
    else:
        base = base.filter(category__is_organization=True)

    return (
        base.exclude(code__in=resolved_eligibility.blocked_membership_type_codes)
        .exclude(category_id__in=resolved_eligibility.pending_membership_category_ids)
        .exclude(group_cn="")
    )


def get_valid_membership_type_codes_for_username(username: str) -> set[str]:
    return get_valid_membership_type_codes(username=username)


def get_extendable_membership_type_codes_for_username(username: str) -> set[str]:
    return get_extendable_membership_type_codes(username=username)
