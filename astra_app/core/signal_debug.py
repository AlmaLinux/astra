"""Debug-only in-process signal ring buffer.

WARNING: per-process and ephemeral. This is not a durable audit log.
"""

import collections
import datetime
import logging as _logging
from typing import Any

from django.conf import settings
from django.db.models import Model
from django.dispatch import Signal

from core.signals import CANONICAL_SIGNALS

_signal_log: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)
_signal_name_by_object: dict[Signal, str] = {
    signal: signal_name
    for signal_name, signal in CANONICAL_SIGNALS.items()
}


def _serialize_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _append_signal_log(*, sender: object, signal: Signal | None, **kwargs: object) -> None:
    signal_name = _signal_name_by_object.get(signal, "unknown") if signal else "unknown"
    payload: dict[str, object] = {
        key: _serialize_value(value)
        for key, value in kwargs.items()
    }
    _signal_log.append(
        {
            "signal": signal_name,
            "sender": _serialize_value(sender),
            "kwargs": payload,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }
    )


def get_signal_log() -> list[dict[str, Any]]:
    return list(_signal_log)


def clear_signal_log() -> None:
    _signal_log.clear()


if settings.DEBUG:
    for _signal_name, _signal in CANONICAL_SIGNALS.items():
        _signal.connect(
            _append_signal_log,
            dispatch_uid=f"core.signal_debug.{_signal_name}",
        )


_emission_logger = _logging.getLogger("core.signals")


def _log_emission(*, sender: object, signal: Signal | None, **kwargs: object) -> None:
    actor = kwargs.get("actor")
    object_ids: dict[str, object] = {}
    for key, value in kwargs.items():
        if key.endswith("_id"):
            if isinstance(value, int | float | str | bool) or value is None:
                object_ids[key] = value
            else:
                object_ids[key] = str(value)
        elif isinstance(value, Model):
            object_ids[f"{type(value).__name__}.pk"] = value.pk

    signal_name = _signal_name_by_object.get(signal, "unknown") if signal else "unknown"
    sender_name = type(sender).__name__ if not isinstance(sender, type) else sender.__name__
    _emission_logger.debug(
        "signal.emit",
        extra={
            "event_key": signal_name,
            "sender": sender_name,
            "actor": str(actor) if actor is not None else None,
            "object_ids": object_ids,
        },
    )


for _signal_name, _signal in CANONICAL_SIGNALS.items():
    _signal.connect(
        _log_emission,
        dispatch_uid=f"core.signal_debug.emission_log.{_signal_name}",
    )
