import logging
from collections.abc import Callable
from dataclasses import dataclass

from django.db import transaction

from core.logging_extras import current_exception_log_fields
from core.membership import (
    FreeIPACallerMode,
    FreeIPAMissingUserPolicy,
    FreeIPARepresentativeSyncError,
    get_valid_memberships,
    rollback_organization_representative_groups,
    sync_organization_representative_groups,
    term_membership_request,
)
from core.membership_notes import add_note
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Organization

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OrganizationRepresentativeTransitionResult:
    organization: Organization
    old_representative: str
    new_representative: str
    changed: bool
    had_active_groups: bool
    targeted_group_cns: tuple[str, ...]


def _record_representative_change(
    *,
    organization: Organization,
    memberships: list[Membership],
    old_representative: str,
    new_representative: str,
    actor_username: str,
) -> None:
    """Log the change on every open request and running membership of the organization, and
    note it on their requests."""
    affected: list[tuple[MembershipType, MembershipRequest | None]] = [
        (request.membership_type, request)
        for request in MembershipRequest.objects.select_related("membership_type").filter(
            requested_organization=organization,
            status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
        )
    ]
    for membership in memberships:
        term_request = term_membership_request(
            membership_type=membership.membership_type,
            target_organization=organization,
        )
        # A membership granted outside the request workflow may have no request; it still changed hands.
        if term_request is None or all(request != term_request for _, request in affected):
            affected.append((membership.membership_type, term_request))

    MembershipLog.objects.bulk_create(
        [
            MembershipLog(
                actor_username=actor_username,
                target_organization=organization,
                membership_type=membership_type,
                membership_request=request,
                action=MembershipLog.Action.representative_changed,
                old_representative=old_representative,
                new_representative=new_representative,
            )
            for membership_type, request in affected
        ]
    )
    for _, request in affected:
        if request is not None:
            add_note(
                membership_request=request,
                username=actor_username,
                action={"type": "representative_changed", "old": old_representative, "new": new_representative},
            )


def apply_organization_representative_transition(
    *,
    organization_id: int,
    new_representative: str,
    caller_label: str,
    actor_username: str,
    persist_changes: Callable[[Organization], None],
) -> OrganizationRepresentativeTransitionResult:
    old_representative = ""
    targeted_group_cns_tuple: tuple[str, ...] = ()
    had_active_groups = False
    changed = False
    sync_journal = None
    persistence_failed = False
    result: OrganizationRepresentativeTransitionResult | None = None

    try:
        with transaction.atomic():
            locked_organization = Organization.objects.select_for_update().get(pk=organization_id)
            old_representative = locked_organization.representative
            targeted_group_cns: list[str] = []
            seen_group_cns: set[str] = set()
            valid_memberships = get_valid_memberships(organization=locked_organization)
            for membership in valid_memberships:
                group_cn = membership.membership_type.group_cn
                if not group_cn or group_cn in seen_group_cns:
                    continue
                seen_group_cns.add(group_cn)
                targeted_group_cns.append(group_cn)
            targeted_group_cns_tuple = tuple(targeted_group_cns)
            had_active_groups = bool(targeted_group_cns_tuple)
            changed = old_representative != new_representative

            locked_organization.representative = new_representative

            if changed and had_active_groups:
                try:
                    sync_result = sync_organization_representative_groups(
                        old_representative=old_representative,
                        new_representative=new_representative,
                        group_cns=targeted_group_cns_tuple,
                        caller_mode=FreeIPACallerMode.raise_on_error,
                        missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                    )
                except FreeIPARepresentativeSyncError as exc:
                    rollback_organization_representative_groups(
                        old_representative=old_representative,
                        new_representative=new_representative,
                        journal=exc.result.journal,
                    )
                    logger.warning(
                        "organization representative transition caller=%s org_id=%s result=sync_failed rollback_result=attempted had_active_groups=%s group_count=%s",
                        caller_label,
                        organization_id,
                        had_active_groups,
                        len(targeted_group_cns_tuple),
                    )
                    raise
                sync_journal = sync_result.journal

            try:
                persist_changes(locked_organization)
                # Part of persisting: if the record fails, the FreeIPA groups are rolled back too.
                if changed:
                    _record_representative_change(
                        organization=locked_organization,
                        memberships=valid_memberships,
                        old_representative=old_representative,
                        new_representative=new_representative,
                        actor_username=actor_username,
                    )
            except Exception:
                persistence_failed = True
                raise

            logger.info(
                "organization representative transition caller=%s org_id=%s result=success changed=%s had_active_groups=%s group_count=%s",
                caller_label,
                organization_id,
                changed,
                had_active_groups,
                len(targeted_group_cns_tuple),
            )
            result = OrganizationRepresentativeTransitionResult(
                organization=locked_organization,
                old_representative=old_representative,
                new_representative=new_representative,
                changed=changed,
                had_active_groups=had_active_groups,
                targeted_group_cns=targeted_group_cns_tuple,
            )
    except Exception:
        if not persistence_failed:
            raise

        rollback_result = "not_needed"
        if changed and sync_journal is not None:
            rollback_organization_representative_groups(
                old_representative=old_representative,
                new_representative=new_representative,
                journal=sync_journal,
            )
            rollback_result = "attempted"
        logger.exception(
            "organization representative transition caller=%s org_id=%s result=persistence_failed rollback_result=%s had_active_groups=%s group_count=%s",
            caller_label,
            organization_id,
            rollback_result,
            had_active_groups,
            len(targeted_group_cns_tuple),
            extra=current_exception_log_fields(),
        )
        raise

    assert result is not None
    return result