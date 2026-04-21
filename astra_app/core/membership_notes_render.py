from django.http import HttpRequest

from core.membership_notes_preload import build_notes_by_membership_request_id
from core.models import MembershipRequest
from core.templatetags import core_membership_notes


def render_membership_notes_widget(
    *,
    request: HttpRequest,
    review_permissions: dict[str, bool],
    membership_request: MembershipRequest,
    compact: bool,
    next_url: str,
) -> str:
    context = {"request": request, **review_permissions}
    notes_by_request_id = build_notes_by_membership_request_id([membership_request.pk])
    html = core_membership_notes.render_membership_request_notes(
        context,
        membership_request,
        compact=compact,
        next_url=next_url,
        notes=list(notes_by_request_id.get(int(membership_request.pk), [])),
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
    aggregate_preloaded_target_user: object | None = None,
) -> str:
    context = {"request": request, **review_permissions}
    if aggregate_preloaded_target_user is not None:
        context["aggregate_preloaded_target_user"] = aggregate_preloaded_target_user
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
