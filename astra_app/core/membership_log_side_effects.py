from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from core.models import Membership, MembershipLog

logger = logging.getLogger(__name__)


def cleanup_orphaned_organization_memberships(*, log: MembershipLog) -> None:
    from core.models import Membership, MembershipLog

    if log.action not in {
        MembershipLog.Action.approved,
        MembershipLog.Action.expiry_changed,
        MembershipLog.Action.terminated,
    }:
        return

    try:
        organization_id = int(log.target_organization_code)
    except ValueError:
        return

    # The organization FK is already NULL (organization deleted), so this code is
    # the only remaining identifier and any stale membership rows for that org
    # must be cleaned up.
    deleted_count, _ = Membership.objects.filter(target_organization_id=organization_id).delete()
    if deleted_count > 0:
        logger.info(
            "cleanup_orphaned_organization_memberships: removed memberships for orphan org_code=%s",
            log.target_organization_code,
        )


def resolve_term_start_at(
    *,
    log: MembershipLog,
    existing: Membership | None,
    log_filter: dict[str, object],
) -> datetime.datetime:
    """Compute the start of the current uninterrupted term from membership logs."""
    from core.models import MembershipLog

    if existing is not None and existing.expires_at is not None and existing.expires_at > log.created_at:
        return existing.created_at

    start_at = log.created_at

    last_approved = (
        MembershipLog.objects.filter(
            **log_filter,
            action=MembershipLog.Action.approved,
            created_at__lt=log.created_at,
        )
        .only("created_at", "expires_at")
        .order_by("-created_at")
        .first()
    )

    if last_approved is not None and last_approved.expires_at is not None and last_approved.expires_at > log.created_at:
        last_terminated = (
            MembershipLog.objects.filter(
                **log_filter,
                action=MembershipLog.Action.terminated,
                created_at__lt=log.created_at,
            )
            .only("created_at")
            .order_by("-created_at")
            .first()
        )

        approved_qs = MembershipLog.objects.filter(**log_filter, action=MembershipLog.Action.approved)
        if last_terminated is not None:
            approved_qs = approved_qs.filter(created_at__gt=last_terminated.created_at)

        first_term_approved = approved_qs.only("created_at").order_by("created_at").first()
        if first_term_approved is not None:
            start_at = first_term_approved.created_at

    return start_at


def apply_org_side_effects(*, log: MembershipLog) -> None:
    from core.models import Membership, MembershipLog, Organization

    if log.action not in {
        MembershipLog.Action.approved,
        MembershipLog.Action.expiry_changed,
        MembershipLog.Action.terminated,
    }:
        return

    if log.action == MembershipLog.Action.terminated:
        Membership.objects.filter(
            target_organization_id=log.target_organization_id,
            membership_type=log.membership_type,
        ).delete()
        return

    existing = (
        Membership.objects.filter(
            target_organization_id=log.target_organization_id,
            membership_type=log.membership_type,
        )
        .only("created_at", "expires_at")
        .first()
    )

    log_filter = log.target_identity.for_membership_log_filter()
    log_filter["membership_type"] = log.membership_type

    start_at = resolve_term_start_at(log=log, existing=existing, log_filter=log_filter)

    organization = Organization.objects.filter(pk=log.target_organization_id).first()
    if organization is None:
        logger.warning(
            "apply_org_side_effects: organization not found org_id=%s",
            log.target_organization_id,
        )
        return

    _new_membership, old = Membership.replace_within_category(
        organization=organization,
        new_membership_type=log.membership_type,
        expires_at=log.expires_at,
        created_at=start_at,
    )
    if old is not None and old.membership_type_id != log.membership_type_id:
        logger.info(
            "apply_org_side_effects: replaced %s with %s for org_id=%s",
            old.membership_type_id,
            log.membership_type_id,
            log.target_organization_id,
        )


def apply_user_side_effects(*, log: MembershipLog) -> None:
    from core.models import Membership, MembershipLog

    if log.action not in {
        MembershipLog.Action.approved,
        MembershipLog.Action.expiry_changed,
        MembershipLog.Action.terminated,
    }:
        return

    category_id = log.membership_type.category_id
    membership_qs = Membership.objects.filter(
        target_username=log.target_username,
        membership_type__category_id=category_id,
    )

    if log.action == MembershipLog.Action.terminated:
        membership_qs.filter(membership_type=log.membership_type).delete()
        return

    existing_same_type = (
        membership_qs.filter(membership_type=log.membership_type)
        .only("created_at", "expires_at")
        .first()
    )
    log_filter = log.target_identity.for_membership_log_filter()
    log_filter["membership_type"] = log.membership_type
    start_at = resolve_term_start_at(log=log, existing=existing_same_type, log_filter=log_filter)

    with transaction.atomic():
        removed_count, _ = membership_qs.exclude(membership_type=log.membership_type).delete()
        row, _created = Membership.objects.update_or_create(
            target_username=log.target_username,
            membership_type=log.membership_type,
            defaults={
                "expires_at": log.expires_at,
            },
        )

        if row.created_at != start_at:
            Membership.objects.filter(pk=row.pk).update(created_at=start_at)

    if removed_count > 0:
        logger.info(
            "apply_user_side_effects: enforced one-per-category target=%s category=%s removed=%s",
            log.target_username,
            category_id,
            removed_count,
        )


def apply_membership_log_side_effects(*, log: MembershipLog) -> None:

    if log.target_organization_id is not None:
        apply_org_side_effects(log=log)
        return

    if log.target_organization_code:
        cleanup_orphaned_organization_memberships(log=log)
        return

    apply_user_side_effects(log=log)
