"""Progress tracking for bulk email runs.

Any operator action that queues hundreds of rendered emails -- opening an
election, reminding an electorate, the Send Mail tool, resending invitations --
takes minutes. The HTTP request that triggers such a run therefore returns as
soon as the recipients are known and hands delivery to a background thread,
while the operator's browser polls the progress record this module keeps.

The record lives in the shared cache (database-backed in this deployment) rather
than in process memory because the polling request is usually served by a
different worker process than the one running delivery.

A record is addressed by its *kind* plus a *scope*: the election id for election
runs, and the operator's username for runs that belong to a person rather than
an object.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from django.core.cache import cache
from django.db import connection

from core.mail_delivery import MailDelivery

logger = logging.getLogger(__name__)

# Keep finished records around long enough for the operator to read the outcome
# after a slow delivery, without pinning them forever.
PROGRESS_TTL_SECONDS = 3600

# A "running" record that has not been touched for this long means the worker
# process running delivery went away; report that instead of polling forever.
PROGRESS_STALE_SECONDS = 120

STALLED_MESSAGE = "Email delivery stopped before it finished. Some recipients were not emailed."
FAILED_MESSAGE = "Email delivery failed. Some recipients were not emailed."


class MailRunKind(StrEnum):
    """Which bulk email run a progress record belongs to.

    Election runs are scoped by election id; the others by operator username.
    """

    election_start = "start"
    election_reminder = "reminder"
    send_mail = "send_mail"
    invitation_resend = "invitation_resend"


class MailRunState(StrEnum):
    running = "running"
    done = "done"
    failed = "failed"
    stalled = "stalled"


@dataclass(slots=True)
class MailRunProgress:
    state: MailRunState = MailRunState.running
    total: int = 0
    processed: int = 0
    emailed: int = 0
    skipped: int = 0
    failures: int = 0
    message: str = ""
    updated_at: float = field(default_factory=time.time)


def _cache_key(scope: str, kind: MailRunKind) -> str:
    return f"mail_progress:{kind}:{scope}"


def write(*, scope: str, kind: MailRunKind, progress: MailRunProgress) -> None:
    progress.updated_at = time.time()
    cache.set(_cache_key(scope, kind), asdict(progress), PROGRESS_TTL_SECONDS)


def read(*, scope: str, kind: MailRunKind) -> MailRunProgress | None:
    """Return the stored progress, reporting an abandoned run as stalled."""
    stored = cache.get(_cache_key(scope, kind))
    if stored is None:
        return None
    progress = MailRunProgress(**stored)
    if progress.state is MailRunState.running and time.time() - progress.updated_at > PROGRESS_STALE_SECONDS:
        progress.state = MailRunState.stalled
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


def forget(*, scope: str, kind: MailRunKind) -> None:
    """Drop a finished record once the operator has seen its outcome."""
    cache.delete(_cache_key(scope, kind))


def deliver_in_background(
    *,
    scope: str,
    kind: MailRunKind,
    total: int,
    deliver: Callable[[Callable[[MailDelivery], None]], MailDelivery],
) -> MailRunProgress:
    """Seed the progress record and run *deliver* off the request.

    *deliver* is handed the progress reporter to pass to the delivery loop.
    Returns the seeded record so the triggering response can hand the client
    something to render before the first poll comes back.
    """
    progress = MailRunProgress(state=MailRunState.running, total=total)
    write(scope=scope, kind=kind, progress=progress)

    def run() -> None:
        _run(scope=scope, kind=kind, deliver=deliver)

    _spawn(run, name=f"mail-run-{kind}-{scope}")
    return progress


def _run(
    *,
    scope: str,
    kind: MailRunKind,
    deliver: Callable[[Callable[[MailDelivery], None]], MailDelivery],
) -> None:
    def report(delivery: MailDelivery) -> None:
        write(
            scope=scope,
            kind=kind,
            progress=MailRunProgress(state=MailRunState.running, **asdict(delivery)),
        )

    try:
        delivery = deliver(report)
    except Exception:
        logger.exception("mail run: %s delivery failed scope=%s", kind, scope)
        failed = read(scope=scope, kind=kind) or MailRunProgress()
        failed.state = MailRunState.failed
        failed.message = FAILED_MESSAGE
        write(scope=scope, kind=kind, progress=failed)
        return

    write(
        scope=scope,
        kind=kind,
        progress=MailRunProgress(state=MailRunState.done, **asdict(delivery)),
    )
