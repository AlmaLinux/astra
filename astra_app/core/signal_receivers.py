"""Signal receiver registry.

Import this module in CoreConfig.ready() to connect all receivers.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from core import signal_debug  # noqa: F401

logger = logging.getLogger("core.signal_receivers")


def safe_receiver(event_key: str) -> Callable[[Callable[..., Any]], Callable[..., None]]:
    """Wrap a signal receiver so failures are logged and never propagated."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., None]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            started_at = time.monotonic()
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                duration_ms = round((time.monotonic() - started_at) * 1000, 1)

                extra: dict[str, object] = {
                    "receiver": fn.__qualname__,
                    "event_key": event_key,
                    "exc_type": type(exc).__name__,
                    "exc_message": str(exc),
                    "duration_ms": duration_ms,
                }
                membership_request = kwargs.get("membership_request")
                if membership_request is not None:
                    try:
                        membership_request_id = membership_request.pk
                    except AttributeError:
                        membership_request_id = None
                    if membership_request_id is not None:
                        extra["membership_request_id"] = membership_request_id

                organization_id = kwargs.get("organization_id")
                if organization_id is not None:
                    extra["organization_id"] = organization_id

                logger.error(
                    "receiver.error",
                    extra=extra,
                    exc_info=True,
                )
                return

            duration_ms = round((time.monotonic() - started_at) * 1000, 1)
            logger.debug(
                "receiver.ok",
                extra={
                    "receiver": fn.__qualname__,
                    "event_key": event_key,
                    "duration_ms": duration_ms,
                },
            )

        return wrapper

    return decorator


def connect_once(fn: Callable[[], None]) -> Callable[[], None]:
    """Ensure a receiver-connect function only executes once (idempotent)."""
    connected = False

    @functools.wraps(fn)
    def wrapper() -> None:
        nonlocal connected
        if connected:
            return
        fn()
        connected = True

    return wrapper


from core import mattermost_webhooks as _mattermost_webhooks  # noqa: E402
from core import membership_notes_receivers as _membership_notes_receivers  # noqa: E402
from core import mirror_membership_validation_receivers as _mirror_membership_validation_receivers  # noqa: E402

_mattermost_webhooks.connect_mattermost_receivers()
_mirror_membership_validation_receivers.connect_mirror_membership_validation_receivers()
_membership_notes_receivers.connect_membership_notes_receivers()
