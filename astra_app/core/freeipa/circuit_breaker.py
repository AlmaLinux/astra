import logging
import socket

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("core.backends")

_FREEIPA_CIRCUIT_OPEN_CACHE_KEY = "freeipa_circuit_open"
_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY = "freeipa_circuit_consecutive_failures"

_ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY = "freeipa_elections_circuit_open"
_ELECTIONS_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY = "freeipa_elections_circuit_consecutive_failures"


def _log_circuit_breaker_transition(
    *,
    breaker_name: str,
    from_state: str,
    to_state: str,
    failure_count: int,
    cooldown_seconds: int,
) -> None:
    logger.warning(
        "astra.freeipa.circuit_breaker.transition breaker_name=%s from_state=%s to_state=%s failure_count=%d cooldown_seconds=%d",
        breaker_name,
        from_state,
        to_state,
        failure_count,
        cooldown_seconds,
        extra={
            "event": "astra.freeipa.circuit_breaker.transition",
            "component": "freeipa",
            "from_state": from_state,
            "to_state": to_state,
            "outcome": "transition",
            "correlation_id": "freeipa.circuit_breaker",
            "breaker_name": breaker_name,
            "failure_count": failure_count,
            "cooldown_seconds": cooldown_seconds,
        },
    )


def _freeipa_circuit_open() -> bool:
    try:
        return bool(cache.get(_FREEIPA_CIRCUIT_OPEN_CACHE_KEY))
    except Exception:
        return False


def _open_freeipa_circuit(*, failure_count: int = 0) -> None:
    cooldown_seconds = settings.FREEIPA_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    was_open = _freeipa_circuit_open()

    try:
        cache.add(_FREEIPA_CIRCUIT_OPEN_CACHE_KEY, True, timeout=cooldown_seconds)
    except Exception:
        return

    if not was_open and _freeipa_circuit_open():
        _log_circuit_breaker_transition(
            breaker_name="freeipa.general",
            from_state="closed",
            to_state="open",
            failure_count=failure_count,
            cooldown_seconds=cooldown_seconds,
        )


def _reset_freeipa_circuit_failures() -> None:
    was_open = _freeipa_circuit_open()

    try:
        cache.delete(_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY)
        cache.delete(_FREEIPA_CIRCUIT_OPEN_CACHE_KEY)
    except Exception:
        return

    if was_open:
        _log_circuit_breaker_transition(
            breaker_name="freeipa.general",
            from_state="open",
            to_state="closed",
            failure_count=0,
            cooldown_seconds=settings.FREEIPA_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )


def _record_freeipa_availability_failure() -> None:
    cooldown_seconds = settings.FREEIPA_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    threshold = settings.FREEIPA_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES

    try:
        cache.add(_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY, 0, timeout=cooldown_seconds)
        failures = int(cache.incr(_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY))
    except Exception:
        return

    if failures >= threshold:
        _open_freeipa_circuit(failure_count=failures)


def _elections_freeipa_circuit_open() -> bool:
    try:
        return bool(cache.get(_ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY))
    except Exception:
        return False


def _open_elections_freeipa_circuit(*, failure_count: int = 0) -> None:
    cooldown_seconds = settings.ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS
    was_open = _elections_freeipa_circuit_open()

    try:
        cache.add(_ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY, True, timeout=cooldown_seconds)
    except Exception:
        return

    if not was_open and _elections_freeipa_circuit_open():
        _log_circuit_breaker_transition(
            breaker_name="freeipa.elections",
            from_state="closed",
            to_state="open",
            failure_count=failure_count,
            cooldown_seconds=cooldown_seconds,
        )


def _reset_elections_freeipa_circuit_failures() -> None:
    was_open = _elections_freeipa_circuit_open()

    try:
        cache.delete(_ELECTIONS_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY)
        cache.delete(_ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY)
    except Exception:
        return

    if was_open:
        _log_circuit_breaker_transition(
            breaker_name="freeipa.elections",
            from_state="open",
            to_state="closed",
            failure_count=0,
            cooldown_seconds=settings.ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS,
        )


def _record_elections_freeipa_availability_failure() -> None:
    cooldown_seconds = settings.ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS
    threshold = settings.FREEIPA_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES

    try:
        cache.add(_ELECTIONS_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY, 0, timeout=cooldown_seconds)
        failures = int(cache.incr(_ELECTIONS_FREEIPA_CIRCUIT_FAILURES_CACHE_KEY))
    except Exception:
        return

    if failures >= threshold:
        _open_elections_freeipa_circuit(failure_count=failures)


def _is_freeipa_availability_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            socket.timeout,
        ),
    )


__all__ = [
    "_freeipa_circuit_open",
    "_reset_freeipa_circuit_failures",
    "_record_freeipa_availability_failure",
    "_elections_freeipa_circuit_open",
    "_reset_elections_freeipa_circuit_failures",
    "_record_elections_freeipa_availability_failure",
    "_is_freeipa_availability_error",
]
