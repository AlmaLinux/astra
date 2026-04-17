import json
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
from core.logging_extras import current_exception_log_fields

logger = logging.getLogger("core.backends")

_service_client_local = threading.local()
_viewer_username_local = threading.local()

_FREEIPA_REQUEST_TIMEOUT_SECONDS: int = settings.FREEIPA_REQUEST_TIMEOUT_SECONDS


def _freeipa_rpc_span_data_from_body(body: object) -> dict[str, object] | None:
    if isinstance(body, bytes):
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(body, str):
        body_text = body
    else:
        return None

    try:
        payload = json.loads(body_text)
    except (TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    method = payload.get("method")
    if not isinstance(method, str):
        return None

    normalized_method = method.strip()
    if not normalized_method:
        return None

    span_data: dict[str, object] = {"freeipa.rpc_method": normalized_method}
    params = payload.get("params")
    if isinstance(params, list):
        if params and isinstance(params[0], list):
            span_data["freeipa.rpc_arg_count"] = len(params[0])
        if len(params) > 1 and isinstance(params[1], dict):
            span_data["freeipa.rpc_option_keys"] = sorted(str(key) for key in params[1])

    return span_data


def _annotate_freeipa_response_span(response: requests.Response, *_args: object, **_kwargs: object) -> requests.Response:
    raw_response = response.raw
    connection = getattr(raw_response, "connection", None) or getattr(raw_response, "_connection", None)
    span = getattr(connection, "_sentrysdk_span", None)
    if span is None:
        return response

    request_body = response.request.body if response.request is not None else None
    span_data = _freeipa_rpc_span_data_from_body(request_body)
    if span_data is None:
        return response

    rpc_method = span_data["freeipa.rpc_method"]
    if isinstance(span.description, str) and span.description:
        suffix = f"[{rpc_method}]"
        if suffix not in span.description:
            span.description = f"{span.description} {suffix}"

    span.set_tag("freeipa.rpc_method", rpc_method)
    span.update_data(span_data)
    return response

class _FreeIPATimeoutSession(requests.Session):
    def __init__(self, default_timeout: float) -> None:
        super().__init__()
        self.default_timeout = default_timeout
        self.hooks["response"].append(_annotate_freeipa_response_span)

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

    def _is_noop_no_modifications(exc: Exception) -> bool:
        return isinstance(exc, exceptions.BadRequest) and "no modifications to be performed" in str(exc).lower()

    try:
        client = get_client()
        result = fn(client)
    except exceptions.PasswordExpired as exc:
        logger.exception(
            "FreeIPA service account password expired: %s",
            exc,
            extra=current_exception_log_fields(),
        )
        clear_freeipa_service_client_cache()
        raise
    except exceptions.Unauthorized:
        clear_freeipa_service_client_cache()
        client = get_client()
        try:
            result = fn(client)
        except Exception as exc:
            if isinstance(exc, exceptions.PasswordExpired):
                clear_freeipa_service_client_cache()
            if _is_freeipa_availability_error(exc):
                _record_freeipa_availability_failure()
            if _is_noop_no_modifications(exc):
                logger.info(
                    "FreeIPA service account operation was a no-op: %s",
                    exc,
                    extra=current_exception_log_fields(),
                )
            else:
                logger.exception(
                    "FreeIPA service account operation failed: %s",
                    exc,
                    extra=current_exception_log_fields(),
                )
            raise
    except Exception as exc:
        if _is_freeipa_availability_error(exc):
            _record_freeipa_availability_failure()
        if _is_noop_no_modifications(exc):
            logger.info(
                "FreeIPA service account operation was a no-op: %s",
                exc,
                extra=current_exception_log_fields(),
            )
        else:
            logger.exception(
                "FreeIPA service account operation failed: %s",
                exc,
                extra=current_exception_log_fields(),
            )
        raise

    _reset_freeipa_circuit_failures()
    return result


__all__ = [
    "_annotate_freeipa_response_span",
    "_FreeIPATimeoutSession",
    "_build_freeipa_client",
    "_freeipa_rpc_span_data_from_body",
    "_get_freeipa_client",
    "_get_freeipa_service_client_cached",
    "clear_freeipa_service_client_cache",
    "reset_freeipa_client",
    "set_current_viewer_username",
    "clear_current_viewer_username",
    "_get_current_viewer_username",
    "_with_freeipa_service_client_retry",
]
