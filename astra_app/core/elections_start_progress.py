"""Progress tracking for the credential-email half of an election start.

Opening an election queues one rendered email per eligible voter, which takes
minutes for a large electorate.  The HTTP request that opens the election
therefore returns as soon as credentials are issued and hands delivery to a
background thread, while the operator's browser polls the progress record this
module keeps.

The record lives in the shared cache (database-backed in this deployment) rather
than in process memory because the polling request is usually served by a
different worker process than the one running delivery.
"""

import datetime
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from django.core.cache import cache
from django.db import connection

from core import elections_services

logger = logging.getLogger(__name__)

# Keep finished records around long enough for the operator to read the outcome
# after a slow delivery, without pinning them forever.
PROGRESS_TTL_SECONDS = 3600

# A "running" record that has not been touched for this long means the worker
# process running delivery went away; report that instead of polling forever.
PROGRESS_STALE_SECONDS = 120

STALLED_MESSAGE = (
    "Credential delivery stopped before it finished. Use Send credential emails "
    "on the election page to deliver the remaining ones."
)
FAILED_MESSAGE = (
    "Credential delivery failed. Use Send credential emails on the election page "
    "to deliver the remaining ones."
)


class ElectionStartState(StrEnum):
    running = "running"
    done = "done"
    failed = "failed"
    stalled = "stalled"


@dataclass(slots=True)
class ElectionStartProgress:
    state: ElectionStartState = ElectionStartState.running
    total: int = 0
    processed: int = 0
    emailed: int = 0
    skipped: int = 0
    failures: int = 0
    message: str = ""
    updated_at: float = field(default_factory=time.time)


def _cache_key(election_id: int) -> str:
    return f"election_start_progress:{election_id}"


def write(*, election_id: int, progress: ElectionStartProgress) -> None:
    progress.updated_at = time.time()
    cache.set(_cache_key(election_id), asdict(progress), PROGRESS_TTL_SECONDS)


def read(*, election_id: int) -> ElectionStartProgress | None:
    """Return the stored progress, reporting an abandoned run as stalled."""
    stored = cache.get(_cache_key(election_id))
    if stored is None:
        return None
    progress = ElectionStartProgress(**stored)
    if progress.state is ElectionStartState.running and time.time() - progress.updated_at > PROGRESS_STALE_SECONDS:
        progress.state = ElectionStartState.stalled
        progress.message = STALLED_MESSAGE
    return progress


def _spawn(target: Callable[[], None], *, name: str) -> None:
    """Run *target* in a daemon thread with its own database connection.

    Django opens a connection per thread and only closes it at the end of a
    request, so the thread has to release it itself.
    """

    def run() -> None:
        try:
            target()
        finally:
            connection.close()

    threading.Thread(target=run, daemon=True, name=name).start()


def deliver_in_background(
    *,
    election_id: int,
    total: int,
    opened_at: datetime.datetime,
    actor: str = "",
    scheduled: bool = False,
) -> ElectionStartProgress:
    """Seed the progress record and queue credential delivery off the request.

    Returns the seeded record so the opening response can hand the client
    something to render before the first poll comes back.
    """
    progress = ElectionStartProgress(state=ElectionStartState.running, total=total)
    write(election_id=election_id, progress=progress)

    def deliver() -> None:
        _deliver(election_id=election_id, opened_at=opened_at, actor=actor, scheduled=scheduled)

    _spawn(deliver, name=f"election-start-delivery-{election_id}")
    return progress


def _deliver(*, election_id: int, opened_at: datetime.datetime, actor: str, scheduled: bool) -> None:
    def report(delivery: elections_services.ElectionStartDelivery) -> None:
        write(
            election_id=election_id,
            progress=ElectionStartProgress(state=ElectionStartState.running, **asdict(delivery)),
        )

    try:
        delivery = elections_services.complete_election_start(
            election_id=election_id,
            scheduled=scheduled,
            opened_at=opened_at,
            actor=actor,
            on_progress=report,
        )
    except Exception:
        logger.exception("election start: credential delivery failed election_id=%s", election_id)
        failed = read(election_id=election_id) or ElectionStartProgress()
        failed.state = ElectionStartState.failed
        failed.message = FAILED_MESSAGE
        write(election_id=election_id, progress=failed)
        return

    write(
        election_id=election_id,
        progress=ElectionStartProgress(state=ElectionStartState.done, **asdict(delivery)),
    )
