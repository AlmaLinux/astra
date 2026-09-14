"""Batched delivery of bulk email runs, with progress reporting.

Every operator-triggered bulk send in the app (election credentials, election
reminders, the Send Mail tool, invitation resends) renders one personalized
email per recipient. This module owns the loop they share: render in batches,
persist each batch, and report how far it has got after each one.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import batched

from post_office.models import Email

from core.templated_email import bulk_save_emails

logger = logging.getLogger(__name__)

# Emails are rendered and persisted in batches so that delivery progress is
# observable (and memory bounded) for a large recipient list. Progress is
# reported once per batch, so this also sets how finely the operator's progress
# bar advances -- it can never be finer than what has actually been written to
# the mail queue.
MAIL_BATCH_SIZE = 10


class MailDeliveryError(Exception):
    """One recipient could not be emailed; the rest of the run continues."""


@dataclass(frozen=True, slots=True)
class MailDelivery:
    """Running totals for a bulk email run."""

    total: int = 0
    processed: int = 0
    emailed: int = 0
    skipped: int = 0
    failures: int = 0


def deliver_in_batches[T](
    *,
    items: Sequence[T],
    prepare: Callable[[T], Email | None],
    skipped: int = 0,
    on_progress: Callable[[MailDelivery], None] | None = None,
) -> MailDelivery:
    """Render and queue one email per item, reporting progress per batch.

    *prepare* returns the unsaved ``Email`` to persist with the batch, or None
    when the item produced no row to persist because it sent the mail itself.
    Raising from *prepare* counts that item as a failure rather than aborting
    the run, so a few bad recipients never stop the rest from being emailed.

    *skipped* seeds the count of recipients the caller filtered out before the
    run, so the totals still cover everyone it set out to email.
    """
    total = len(items) + skipped
    processed = skipped
    emailed = 0
    failures = 0

    def report() -> None:
        if on_progress is not None:
            on_progress(
                MailDelivery(
                    total=total,
                    processed=processed,
                    emailed=emailed,
                    skipped=skipped,
                    failures=failures,
                )
            )

    for batch in batched(items, MAIL_BATCH_SIZE):
        pending_emails: list[Email] = []
        for item in batch:
            processed += 1
            try:
                prepared = prepare(item)
            except Exception:
                logger.exception("mail delivery: preparing an email failed")
                failures += 1
                continue
            if prepared is not None:
                pending_emails.append(prepared)
            emailed += 1

        bulk_save_emails(pending_emails)
        report()

    if not items:
        report()

    return MailDelivery(
        total=total,
        processed=processed,
        emailed=emailed,
        skipped=skipped,
        failures=failures,
    )
