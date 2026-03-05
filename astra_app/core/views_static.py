from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET

from core.freeipa.agreement import FreeIPAFASAgreement


@require_GET
def privacy_policy(request: HttpRequest) -> HttpResponse:
    policy_path = Path(settings.BASE_DIR).parent / "docs" / "privacy-policy.md"

    try:
        raw = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Http404("Privacy policy not available.") from exc

    html = markdown.markdown(raw, extensions=["extra", "sane_lists"])

    return render(
        request,
        "core/legal_markdown.html",
        {
            "title": "Privacy Policy",
            # Content is repository-managed and not user-supplied.
            "content_html": mark_safe(html),
        },
    )


@require_GET
def coc_redirect(request: HttpRequest) -> HttpResponse:
    cn = settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN
    return redirect("agreement-detail", cn=cn)


@require_GET
def robots_txt(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@require_GET
def agreement_detail(request: HttpRequest, cn: str) -> HttpResponse:
    agreement = FreeIPAFASAgreement.get(cn)
    if not agreement or not agreement.enabled:
        raise Http404("Agreement not found.")

    html = markdown.markdown(agreement.description, extensions=["extra", "sane_lists"])

    return render(
        request,
        "core/legal_markdown.html",
        {
            "title": agreement.cn,
            "content_html": mark_safe(html),
        },
    )
