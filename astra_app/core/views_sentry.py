import json
import logging
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

_SENTRY_TUNNEL_TIMEOUT_SECONDS = 5


def _parse_sentry_dsn(dsn: str) -> tuple[str, str, int | None, str, str] | None:
    parsed = urlsplit(dsn)
    project_id = parsed.path.strip("/")
    if not parsed.scheme or not parsed.hostname or not parsed.username or not project_id:
        return None

    return (parsed.scheme, parsed.hostname, parsed.port, parsed.username, project_id)


def _configured_sentry_envelope_url() -> str | None:
    parsed_dsn = _parse_sentry_dsn(settings.SENTRY_DSN)
    if parsed_dsn is None:
        return None

    scheme, hostname, port, _public_key, project_id = parsed_dsn
    netloc = f"{hostname}:{port}" if port else hostname
    return f"{scheme}://{netloc}/api/{project_id}/envelope/"


def _read_envelope_dsn(request: HttpRequest) -> str | None:
    if not request.body:
        return None

    header_line = request.body.split(b"\n", maxsplit=1)[0]
    if not header_line:
        return None

    try:
        header_payload = json.loads(header_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    dsn = header_payload.get("dsn")
    return str(dsn).strip() if dsn else None


@csrf_exempt
@require_POST
def sentry_browser_tunnel(request: HttpRequest) -> HttpResponse:
    upstream_url = _configured_sentry_envelope_url()
    expected_dsn = _parse_sentry_dsn(settings.SENTRY_DSN)
    request_dsn = _parse_sentry_dsn(_read_envelope_dsn(request) or "")

    if upstream_url is None or expected_dsn is None or request_dsn != expected_dsn:
        return JsonResponse({"ok": False, "error": "Invalid Sentry envelope DSN."}, status=400)

    try:
        upstream_response = requests.post(
            upstream_url,
            data=request.body,
            headers={"Content-Type": request.content_type or "application/x-sentry-envelope"},
            timeout=_SENTRY_TUNNEL_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException:
        logger.warning("Failed to forward Sentry envelope", exc_info=True)
        return JsonResponse({"ok": False, "error": "Failed to forward Sentry envelope."}, status=502)

    response = HttpResponse(
        content=upstream_response.content,
        status=upstream_response.status_code,
        content_type=upstream_response.headers.get("Content-Type", "text/plain"),
    )
    return response