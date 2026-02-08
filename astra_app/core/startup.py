import logging

import requests

from core.backends import FreeIPAGroup, FreeIPAUnavailableError
from core.protected_resources import membership_type_group_cns

logger = logging.getLogger(__name__)

_membership_groups_synced: bool = False


def ensure_membership_type_groups_exist() -> None:
    """Ensure membership-type groups exist and are not FAS groups.

    This is intended to run once at web process startup (WSGI init). We avoid
    doing FreeIPA lookups on every membership approval.
    """

    global _membership_groups_synced
    if _membership_groups_synced:
        return

    group_cns = sorted(membership_type_group_cns())
    if not group_cns:
        _membership_groups_synced = True
        return

    try:
        for cn in group_cns:
            group = FreeIPAGroup.get(cn)
            if group is None:
                logger.info("Startup: creating missing membership group %r", cn)
                FreeIPAGroup.create(cn=cn, fas_group=False)
                continue

            if bool(group.fas_group):
                raise ValueError(
                    f"Membership type group {cn!r} is a FAS group; refusing to start",
                )
    except (requests.exceptions.ConnectionError, FreeIPAUnavailableError):
        logger.warning(
            "Startup: FreeIPA unavailable; skipping membership group sync",
            exc_info=True,
        )
        return

    _membership_groups_synced = True
