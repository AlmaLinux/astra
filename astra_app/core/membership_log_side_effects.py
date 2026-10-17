from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

if TYPE_CHECKING:
    from core.models import Membership, MembershipLog


def resolve_term_start_at(
    *,
    log: MembershipLog,
    existing: Membership | None,
    log_filter: dict[str, object],
) -> datetime.datetime:
    """Compute the start of the current uninterrupted term from membership logs."""
    from core.models import MembershipLog

    if existing is not None and existing.expires_at > log.created_at:
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

    if last_approved is not None and last_approved.expires_at > log.created_at:
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


def apply_membership_log_side_effects(*, log: MembershipLog) -> None:
    """Mirror a log entry onto the target's membership in the log's category.

    An approval of another type replaces the row; any other change edits it in place.
    Either way the term start carries over while the membership is uninterrupted.
    """
    from core.models import Membership, MembershipLog, MembershipTypeCategory

    if log.action not in {
        MembershipLog.Action.approved,
        MembershipLog.Action.expiry_changed,
        MembershipLog.Action.dues_changed,
        MembershipLog.Action.terminated,
    }:
        return
    if not log.target_username and log.target_organization_id is None:
        # The org is deleted: CASCADE already removed its memberships.
        return

    target_filter = log.target_identity.for_membership_filter()
    category_id = log.membership_type.category_id
    with transaction.atomic():
        # Membership.save() takes the same lock; taking it first keeps the row read below current.
        MembershipTypeCategory.objects.select_for_update().only("pk").get(pk=category_id)
        category_memberships = Membership.objects.filter(**target_filter, membership_type__category_id=category_id)

        if log.action == MembershipLog.Action.terminated:
            category_memberships.filter(membership_type=log.membership_type).delete()
            return

        existing = category_memberships.first()
        # Only an approval grants a membership or changes its type; other actions edit the held one.
        if log.action != MembershipLog.Action.approved and (
            existing is None or existing.membership_type_id != log.membership_type_id
        ):
            raise ValidationError(f"There is no {log.membership_type.name} membership to change.")

        start_at = resolve_term_start_at(
            log=log,
            existing=existing,
            log_filter={
                **log.target_identity.for_membership_log_filter(),
                "membership_type__category_id": category_id,
            },
        )

        if existing is not None and existing.membership_type_id == log.membership_type_id:
            existing.expires_at = log.expires_at
            existing.dues_usd = log.dues_usd
            existing.created_at = start_at
            existing.save(update_fields=["expires_at", "dues_usd", "created_at", "updated_at"])
            return

        category_memberships.delete()
        Membership.objects.create(
            **target_filter,
            membership_type=log.membership_type,
            expires_at=log.expires_at,
            dues_usd=log.dues_usd,
            created_at=start_at,
        )
