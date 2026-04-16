from django.http import HttpRequest

from core.templatetags import core_membership_notes


def render_membership_notes_widget(
    *,
    request: HttpRequest,
    review_permissions: dict[str, bool],
    membership_request: object,
    compact: bool,
    next_url: str,
) -> str:
    context = {"request": request, **review_permissions}
    html = core_membership_notes.membership_notes(
        context,
        membership_request,
        compact=compact,
        next_url=next_url,
    )
    return str(html)


def render_membership_notes_aggregate_widget(
    *,
    request: HttpRequest,
    review_permissions: dict[str, bool],
    target_type: str,
    target: str,
    compact: bool,
    next_url: str,
) -> str:
    context = {"request": request, **review_permissions}
    if target_type == "user":
        html = core_membership_notes.membership_notes_aggregate_for_user(
            context,
            target,
            compact=compact,
            next_url=next_url,
        )
        return str(html)

    html = core_membership_notes.membership_notes_aggregate_for_organization(
        context,
        int(target),
        compact=compact,
        next_url=next_url,
    )
    return str(html)
