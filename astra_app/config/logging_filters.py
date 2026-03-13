import logging
import re

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