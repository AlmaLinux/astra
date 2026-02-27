import logging
import threading
from collections.abc import Callable
from typing import override

import requests
from django.conf import settings
from python_freeipa import ClientMeta, exceptions

from core.freeipa.circuit_breaker import (
    _freeipa_circuit_open,
    _is_freeipa_availability_error,
    _record_freeipa_availability_failure,
    _reset_freeipa_circuit_failures,
)
from core.freeipa.exceptions import FreeIPAUnavailableError

logger = logging.getLogger("core.backends")

_service_client_local = threading.local()
_viewer_username_local = threading.local()

_FREEIPA_REQUEST_TIMEOUT_SECONDS: int = settings.FREEIPA_REQUEST_TIMEOUT_SECONDS

class _FreeIPATimeoutSession(requests.Session):
    def __init__(self, default_timeout: float) -> None:
        super().__init__()
        self.default_timeout = default_timeout

    @override
    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        if "timeout" not in kwargs or kwargs.get("timeout") is None:
            kwargs["timeout"] = self.default_timeout
        return super().request(method, url, **kwargs)


def _build_freeipa_client() -> ClientMeta:
    client = ClientMeta(host=settings.FREEIPA_HOST, verify_ssl=settings.FREEIPA_VERIFY_SSL)
    client._session = _FreeIPATimeoutSession(_FREEIPA_REQUEST_TIMEOUT_SECONDS)
    return client


def _get_freeipa_client(username: str, password: str) -> ClientMeta:
    client = _build_freeipa_client()
    client.login(username, password)
    return client


def _get_freeipa_service_client_cached() -> ClientMeta:
    if hasattr(_service_client_local, "client"):
        client = _service_client_local.client
        if client is not None:
            return client

    client = _get_freeipa_client(settings.FREEIPA_SERVICE_USER, settings.FREEIPA_SERVICE_PASSWORD)
    _service_client_local.client = client
    return client


def clear_freeipa_service_client_cache() -> None:
    if hasattr(_service_client_local, "client"):
        delattr(_service_client_local, "client")


def reset_freeipa_client() -> None:
    """Force a fresh FreeIPA connection on the next request.

    Call this at the top of long management-command loops so each iteration
    starts with a live connection rather than a potentially stale or
    timed-out one.
    """
    clear_freeipa_service_client_cache()


def set_current_viewer_username(username: str | None) -> None:
    if username is None:
        if hasattr(_viewer_username_local, "username"):
            delattr(_viewer_username_local, "username")
        return

    normalized = str(username).strip()
    if not normalized:
        if hasattr(_viewer_username_local, "username"):
            delattr(_viewer_username_local, "username")
        return

    _viewer_username_local.username = normalized


def clear_current_viewer_username() -> None:
    if hasattr(_viewer_username_local, "username"):
        delattr(_viewer_username_local, "username")


def _get_current_viewer_username() -> str | None:
    if not hasattr(_viewer_username_local, "username"):
        return None

    username = _viewer_username_local.username
    if isinstance(username, str) and username.strip():
        return username
    return None


def _with_freeipa_service_client_retry[T](get_client: Callable[[], ClientMeta], fn: Callable[[ClientMeta], T]) -> T:
    if _freeipa_circuit_open():
        raise FreeIPAUnavailableError("FreeIPA circuit breaker is open")

    try:
        client = get_client()
        result = fn(client)
    except exceptions.PasswordExpired as exc:
        logger.exception("FreeIPA service account password expired: %s", exc)
        clear_freeipa_service_client_cache()
        raise
    except exceptions.Unauthorized:
        clear_freeipa_service_client_cache()
        client = get_client()
        try:
            result = fn(client)
        except Exception as exc:
            if _is_freeipa_availability_error(exc):
                _record_freeipa_availability_failure()
            logger.exception("FreeIPA service account operation failed: %s", exc)
            raise
    except Exception as exc:
        if _is_freeipa_availability_error(exc):
            _record_freeipa_availability_failure()
        logger.exception("FreeIPA service account operation failed: %s", exc)
        raise

    _reset_freeipa_circuit_failures()
    return result


__all__ = [
    "_FreeIPATimeoutSession",
    "_build_freeipa_client",
    "_get_freeipa_client",
    "_get_freeipa_service_client_cached",
    "clear_freeipa_service_client_cache",
    "reset_freeipa_client",
    "set_current_viewer_username",
    "clear_current_viewer_username",
    "_get_current_viewer_username",
    "_with_freeipa_service_client_retry",
]
