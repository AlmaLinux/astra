import logging
import time

import requests

from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.group import FreeIPAGroup
from core.protected_resources import membership_type_group_cns

logger = logging.getLogger(__name__)

_membership_groups_synced: bool = False

_STARTUP_MAX_ATTEMPTS: int = 3
_STARTUP_RETRY_BASE_DELAY_SECONDS: float = 5.0


def ensure_membership_type_groups_exist() -> None:
    """Ensure membership-type groups exist and are not FAS groups.

    Retries up to _STARTUP_MAX_ATTEMPTS times with exponential backoff on
    transient FreeIPA failures. If all attempts fail, logs at ERROR level
    (alertable) and returns — startup continues, but group sync will be
    reattempted on the first incoming request.
    """

    global _membership_groups_synced
    if _membership_groups_synced:
        return

    group_cns = sorted(membership_type_group_cns())
    if not group_cns:
        _membership_groups_synced = True
        return

    last_exc: Exception | None = None
    for attempt in range(1, _STARTUP_MAX_ATTEMPTS + 1):
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

            _membership_groups_synced = True
            return
        except (requests.exceptions.ConnectionError, FreeIPAUnavailableError) as exc:
            last_exc = exc
            if attempt < _STARTUP_MAX_ATTEMPTS:
                delay = _STARTUP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Startup: FreeIPA unavailable (attempt %d/%d); retrying in %.0fs",
                    attempt,
                    _STARTUP_MAX_ATTEMPTS,
                    delay,
                    exc_info=True,
                )
                time.sleep(delay)

    logger.error(
        "Startup: FreeIPA unavailable after %d attempts; skipping membership group sync."
        " Will retry on first request.",
        _STARTUP_MAX_ATTEMPTS,
        exc_info=last_exc,
    )
