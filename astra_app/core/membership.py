import datetime
import logging
from collections.abc import Iterable

from django.conf import settings
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import Membership, MembershipLog, MembershipRequest, Organization

logger = logging.getLogger(__name__)


def get_valid_memberships(
    *,
    username: str | None = None,
    organization: Organization | None = None,
) -> list[Membership]:
    """Return the current unexpired active memberships for a user or organization."""

    if (username is None) == (organization is None):
        raise ValueError("Provide exactly one of username or organization.")

    search = {}
    if username is not None:
        normalized_username = str(username or "").strip()
        if not normalized_username:
            return []
        search["target_username"] = normalized_username
    else:
        search["target_organization"] = organization

    memberships = (
        Membership.objects.select_related("membership_type", "membership_type__category")
        .filter(**search)
        .active()
        .order_by(
            "membership_type__category__sort_order",
            "membership_type__category__name",
            "membership_type__sort_order",
            "membership_type__code",
            "membership_type__pk",
        )
    )

    return list(memberships)


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

    if (username is None) == (organization is None):
        raise ValueError("Provide either username or organization, but not both.")

    normalized_username = ""
    if username is not None:
        normalized_username = str(username or "").strip()
        if not normalized_username:
            return {}

    if membership_type_ids is None:
        if username is not None:
            membership_type_ids = Membership.objects.filter(
                target_username=normalized_username,
            ).values_list("membership_type_id", flat=True)
        else:
            membership_type_ids = Membership.objects.filter(
                target_organization=organization,
            ).values_list("membership_type_id", flat=True)

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
    if username is not None:
        log_filters["target_username"] = normalized_username
    else:
        log_filters["target_organization"] = organization

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
        if username is not None:
            request_filters["requested_username"] = normalized_username
        else:
            request_filters["requested_organization"] = organization

        approved_requests = (
            MembershipRequest.objects.filter(**request_filters)
            .only("pk", "membership_type_id", "decided_at", "requested_at")
            .order_by("-decided_at", "-requested_at", "-pk")
        )
        for req in approved_requests:
            request_id_by_membership_type_id.setdefault(req.membership_type_id, int(req.pk))

    return request_id_by_membership_type_id


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


def get_valid_membership_type_codes_for_username(username: str) -> set[str]:
    return {log.membership_type_id for log in get_valid_memberships(username=username)}


def get_extendable_membership_type_codes_for_username(username: str) -> set[str]:
    expiring_soon_by = expiring_soon_cutoff()
    return {
        log.membership_type_id
        for log in get_valid_memberships(username=username)
        if log.expires_at and log.expires_at <= expiring_soon_by
    }
