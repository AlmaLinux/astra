from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET


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
