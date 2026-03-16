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
)
from core.models import Organization

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OrganizationRepresentativeTransitionResult:
    organization: Organization
    old_representative: str
    new_representative: str
    changed: bool
    had_active_groups: bool
    targeted_group_cns: tuple[str, ...]


def apply_organization_representative_transition(
    *,
    organization_id: int,
    new_representative: str,
    caller_label: str,
    persist_changes: Callable[[Organization], None],
) -> OrganizationRepresentativeTransitionResult:
    normalized_new_representative = str(new_representative or "").strip()

    with transaction.atomic():
        locked_organization = Organization.objects.select_for_update().get(pk=organization_id)
        old_representative = str(locked_organization.representative or "").strip()
        targeted_group_cns: list[str] = []
        seen_group_cns: set[str] = set()
        for membership in get_valid_memberships(organization=locked_organization):
            group_cn = str(membership.membership_type.group_cn or "").strip()
            if not group_cn or group_cn in seen_group_cns:
                continue
            seen_group_cns.add(group_cn)
            targeted_group_cns.append(group_cn)
        targeted_group_cns_tuple = tuple(targeted_group_cns)
        had_active_groups = bool(targeted_group_cns_tuple)
        changed = old_representative != normalized_new_representative

        locked_organization.representative = normalized_new_representative

        sync_journal = None
        if changed and had_active_groups:
            try:
                sync_result = sync_organization_representative_groups(
                    old_representative=old_representative,
                    new_representative=normalized_new_representative,
                    group_cns=targeted_group_cns_tuple,
                    caller_mode=FreeIPACallerMode.raise_on_error,
                    missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                )
            except FreeIPARepresentativeSyncError as exc:
                rollback_organization_representative_groups(
                    old_representative=old_representative,
                    new_representative=normalized_new_representative,
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
        except Exception:
            rollback_result = "not_needed"
            if changed and sync_journal is not None:
                rollback_organization_representative_groups(
                    old_representative=old_representative,
                    new_representative=normalized_new_representative,
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

        logger.info(
            "organization representative transition caller=%s org_id=%s result=success changed=%s had_active_groups=%s group_count=%s",
            caller_label,
            organization_id,
            changed,
            had_active_groups,
            len(targeted_group_cns_tuple),
        )
        return OrganizationRepresentativeTransitionResult(
            organization=locked_organization,
            old_representative=old_representative,
            new_representative=normalized_new_representative,
            changed=changed,
            had_active_groups=had_active_groups,
            targeted_group_cns=targeted_group_cns_tuple,
        )