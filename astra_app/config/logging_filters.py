import logging
import re

from config.logging_context import get_request_log_context
from core.logging_extras import exception_log_fields

_REQUEST_LINE_PATTERN = re.compile(r'"[A-Z]+ (?P<path>/[^ ]*) HTTP/[0-9.]+"')
_HETRIX_USER_AGENT = "HetrixTools Uptime Monitoring Bot. https://hetrix.tools/uptime-monitoring-bot.html"


def _extract_request_path(message: str) -> str | None:
    match = _REQUEST_LINE_PATTERN.search(message)
    if not match:
        return None
    return match.group("path")


def _is_root_or_login_path(path: str) -> bool:
    return (
        path == "/"
        or path == "/login"
        or path.startswith("/login?")
        or path.startswith("/login/")
    )


def _extract_exception_from_args(args: object) -> BaseException | None:
    if isinstance(args, BaseException):
        return args

    if isinstance(args, tuple | list):
        for value in args:
            if isinstance(value, BaseException):
                return value
        return None

    if isinstance(args, dict):
        for value in args.values():
            if isinstance(value, BaseException):
                return value

    return None


def _extract_record_exception(record: logging.LogRecord) -> BaseException | None:
    exc_info = record.exc_info
    if exc_info and isinstance(exc_info[1], BaseException):
        return exc_info[1]

    return _extract_exception_from_args(record.args)


def _set_missing_exception_log_fields(record: logging.LogRecord, error: BaseException) -> None:
    for key, value in exception_log_fields(error).items():
        if not hasattr(record, key):
            setattr(record, key, value)


class HealthEndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/healthz" in message or "/readyz" in message:
            return " 200 " not in message
        return True


class HetrixAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _HETRIX_USER_AGENT not in message:
            return True

        request_path = _extract_request_path(message)
        if request_path is None:
            return True

        return not _is_root_or_login_path(request_path)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = get_request_log_context()
        if context:
            # Keep explicit logger-provided fields when present.
            if not hasattr(record, "client_ip") and context["client_ip"] is not None:
                record.client_ip = context["client_ip"]
            if not hasattr(record, "user_id") and context["user_id"] is not None:
                record.user_id = context["user_id"]
            if not hasattr(record, "request_id") and context["request_id"] is not None:
                record.request_id = context["request_id"]
            if not hasattr(record, "request_path"):
                record.request_path = context["request_path"]
            if not hasattr(record, "request_method"):
                record.request_method = context["request_method"]

        error = _extract_record_exception(record)
        if error is not None:
            _set_missing_exception_log_fields(record, error)

        return True