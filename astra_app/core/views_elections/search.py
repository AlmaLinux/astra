"""Election user-search and email preview endpoints."""

import datetime

from django.http import Http404, HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core import elections_eligibility
from core.backends import FreeIPAUser
from core.elections_eligibility import ElectionEligibilityError
from core.email_context import election_committee_email_context
from core.models import Election
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required
from core.templated_email import render_templated_email_preview_response
from core.user_labels import user_label
from core.views_elections._helpers import _election_email_preview_context, _get_active_election


def _parse_search_start_datetime(request: HttpRequest) -> datetime.datetime | None:
    """Parse start_datetime from GET params, defaulting to now. Returns None for invalid dates."""
    raw = str(request.GET.get("start_datetime") or "").strip()
    if not raw:
        return timezone.now()
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _build_user_search_results(
    eligible_usernames: set[str], q: str, *, limit: int = 20,
) -> list[dict[str, str]]:
    """Build sorted, filtered user search results with FreeIPA full-name lookups.

    Degrades gracefully when FreeIPA is unavailable by returning usernames
    without full names.
    """
    q_lower = q.lower()
    results: list[dict[str, str]] = []
    for username in sorted(eligible_usernames, key=str.lower):
        if q_lower and q_lower not in username.lower():
            continue
        try:
            user = FreeIPAUser.get(username)
        except Exception:
            user = None
        results.append({"id": username, "text": user_label(username, user=user)})
        if len(results) >= limit:
            break
    return results


def _resolve_election_for_search(
    request: HttpRequest,
    election_id: int,
    *,
    extra_fields: tuple[str, ...] = (),
    eligible_group_cn_override: str = "",
) -> Election | None:
    """Resolve an Election for the user-search endpoints.

    Returns an Election instance, or None if election_id == 0 and the
    start_datetime param is invalid (caller should return empty results).
    """
    if election_id == 0:
        start_dt = _parse_search_start_datetime(request)
        if start_dt is None:
            return None
        return Election(
            name="", description="", url="",
            start_datetime=start_dt, end_datetime=start_dt,
            number_of_seats=1, status=Election.Status.draft,
            eligible_group_cn=eligible_group_cn_override,
        )
    fields = ("id", "start_datetime", *extra_fields)
    election = (
        Election.objects.active()
        .filter(pk=election_id)
        .only(*fields)
        .first()
    )
    if election is None:
        raise Http404
    return election


@require_POST
@json_permission_required(ASTRA_ADD_ELECTION)
def election_email_render_preview(request, election_id: int) -> JsonResponse:
    def _parse_datetime_local(value: str) -> datetime.datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    if election_id == 0:
        start_dt = _parse_datetime_local(str(request.POST.get("start_datetime") or "")) or timezone.now()
        end_dt = _parse_datetime_local(str(request.POST.get("end_datetime") or "")) or start_dt
        number_of_seats_raw = str(request.POST.get("number_of_seats") or "").strip()
        try:
            number_of_seats = int(number_of_seats_raw)
        except ValueError:
            number_of_seats = 1

        election = Election(
            id=0,
            name=str(request.POST.get("name") or ""),
            description=str(request.POST.get("description") or ""),
            url=str(request.POST.get("url") or ""),
            start_datetime=start_dt,
            end_datetime=end_dt,
            number_of_seats=number_of_seats,
            status=Election.Status.draft,
            eligible_group_cn=str(request.POST.get("eligible_group_cn") or ""),
        )
    else:
        election = _get_active_election(election_id)

        # Allow the live preview to reflect unsaved edits on the page.
        name_raw = str(request.POST.get("name") or "")
        if name_raw:
            election.name = name_raw
        description_raw = str(request.POST.get("description") or "")
        if description_raw:
            election.description = description_raw
        url_raw = str(request.POST.get("url") or "")
        if url_raw:
            election.url = url_raw

        start_dt = _parse_datetime_local(str(request.POST.get("start_datetime") or ""))
        if start_dt is not None:
            election.start_datetime = start_dt
        end_dt = _parse_datetime_local(str(request.POST.get("end_datetime") or ""))
        if end_dt is not None:
            election.end_datetime = end_dt

        number_of_seats_raw = str(request.POST.get("number_of_seats") or "").strip()
        if number_of_seats_raw:
            try:
                election.number_of_seats = int(number_of_seats_raw)
            except ValueError:
                pass
        eligible_group_raw = str(request.POST.get("eligible_group_cn") or "")
        if eligible_group_raw or "eligible_group_cn" in request.POST:
            election.eligible_group_cn = eligible_group_raw

    context: dict[str, object] = {
        **_election_email_preview_context(request=request, election=election),
        **election_committee_email_context(),
    }

    return render_templated_email_preview_response(request=request, context=context)


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def election_eligible_users_search(request, election_id: int):
    eligible_group_cn = str(request.GET.get("eligible_group_cn") or "").strip()

    election = _resolve_election_for_search(
        request, election_id, extra_fields=("eligible_group_cn",),
    )
    if election is None:
        return JsonResponse({"results": []})

    # Allow the edit UI to preview eligibility before the draft is saved,
    # including clearing the field (empty string).
    group_override: str | None = None
    if "eligible_group_cn" in request.GET:
        group_override = eligible_group_cn

    try:
        eligible_usernames = elections_eligibility.eligible_candidate_usernames(
            election=election,
            eligible_group_cn=group_override,
        )
    except ElectionEligibilityError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)

    count_only = str(request.GET.get("count_only") or "").strip()
    if count_only in {"1", "true", "True", "yes", "on"}:
        return JsonResponse({"count": len(eligible_usernames)})

    q = str(request.GET.get("q") or "").strip()
    return JsonResponse({"results": _build_user_search_results(eligible_usernames, q)})


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def election_nomination_users_search(request, election_id: int):
    """Eligible-user search for the nomination UI.

    Nomination eligibility is based on membership age/status only and must not
    be filtered by the election's optional eligible-group voting restriction.
    """
    election = _resolve_election_for_search(
        request, election_id, eligible_group_cn_override="",
    )
    if election is None:
        return JsonResponse({"results": []})

    try:
        eligible_usernames = elections_eligibility.eligible_nominator_usernames(election=election)
    except ElectionEligibilityError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)

    q = str(request.GET.get("q") or "").strip()
    return JsonResponse({"results": _build_user_search_results(eligible_usernames, q)})
