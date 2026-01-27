from __future__ import annotations

import logging

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def healthz(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def readyz(_request: HttpRequest) -> JsonResponse:
    try:
        connection.ensure_connection()
    except Exception as exc:
        logger.exception("Health check readyz failed")
        return JsonResponse({"status": "not ready", "error": str(exc)}, status=503)

    return JsonResponse({"status": "ready", "database": "ok"})