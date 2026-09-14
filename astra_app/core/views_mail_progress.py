"""Polling endpoints for an operator's own bulk email runs.

Election runs are addressed by election id and served from
``core.views_elections.lifecycle``. The runs here belong to the person who
started them -- the Send Mail tool and invitation resends -- so they are scoped
by the requesting username and never name a scope in the request.
"""

from dataclasses import asdict

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core import mail_progress
from core.mail_progress import MailRunKind
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL, _has_permission
from core.views_utils import get_username

# Each personal run kind is readable by whoever is allowed to start it.
RUN_KIND_PERMISSIONS: dict[MailRunKind, str] = {
    MailRunKind.send_mail: ASTRA_ADD_SEND_MAIL,
    MailRunKind.invitation_resend: ASTRA_ADD_MEMBERSHIP,
}


def _resolve_run(request: HttpRequest) -> tuple[str, MailRunKind] | JsonResponse:
    raw_kind = str(request.GET.get("kind") or request.POST.get("kind") or "").strip()
    kind = next((candidate for candidate in RUN_KIND_PERMISSIONS if candidate == raw_kind), None)
    if kind is None:
        return JsonResponse({"ok": False, "errors": ["Unknown email run."]}, status=400)

    username = get_username(request)
    if not username or not _has_permission(user=request.user, permission=RUN_KIND_PERMISSIONS[kind]):
        return JsonResponse({"ok": False, "errors": ["Permission denied."]}, status=403)

    return username, kind


@require_GET
def mail_progress_api(request: HttpRequest) -> JsonResponse:
    """Report how far the caller's own bulk email run has got."""
    resolved = _resolve_run(request)
    if isinstance(resolved, JsonResponse):
        return resolved
    username, kind = resolved

    progress = mail_progress.read(scope=username, kind=kind)
    return JsonResponse({"ok": True, "mail_progress": asdict(progress) if progress is not None else None})


@require_POST
def mail_progress_ack_api(request: HttpRequest) -> JsonResponse:
    """Drop the caller's finished run once they have seen its outcome."""
    resolved = _resolve_run(request)
    if isinstance(resolved, JsonResponse):
        return resolved
    username, kind = resolved

    mail_progress.forget(scope=username, kind=kind)
    return JsonResponse({"ok": True, "mail_progress": None})
