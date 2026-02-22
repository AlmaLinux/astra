"""Election audit log, public ballots, and public audit data exports."""

import datetime
from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Sum
from django.db.models.functions import TruncDate
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import elections_services
from core.elections_sankey import build_sankey_flows
from core.elections_services import candidate_username_by_id_map
from core.models import AuditLogEntry, Ballot, Candidate, Election
from core.permissions import ASTRA_ADD_ELECTION
from core.views_elections._helpers import _elected_candidate_display, _get_active_election, _tally_elected_ids
from core.views_utils import build_url_for_page


def _get_exportable_election(*, election_id: int) -> Election:
    election = (
        Election.objects.active()
        .filter(pk=election_id)
        .only("id", "status", "public_ballots_file", "public_audit_file")
        .first()
    )
    if election is None:
        raise Http404
    if election.status not in {Election.Status.closed, Election.Status.tallied}:
        raise Http404
    return election


@require_GET
def election_public_ballots(request, election_id: int):
    election = _get_exportable_election(election_id=election_id)

    if election.status == Election.Status.tallied and election.public_ballots_file:
        return redirect(election.public_ballots_file.url)

    return JsonResponse(elections_services.build_public_ballots_export(election=election))


@require_GET
def election_public_audit(request, election_id: int):
    election = _get_exportable_election(election_id=election_id)

    if election.status == Election.Status.tallied and election.public_audit_file:
        return redirect(election.public_audit_file.url)

    return JsonResponse(elections_services.build_public_audit_export(election=election))


@require_GET
def election_audit_log(request, election_id: int):
    """Render a human-readable election audit log.

    This page is meant to improve transparency and auditability by presenting
    the election's public audit events (and, for election managers, private
    events as well) in a chronological timeline.
    """

    election = _get_active_election(election_id)

    if election.status not in {Election.Status.closed, Election.Status.tallied}:
        raise Http404

    candidates = list(
        Candidate.objects.filter(election=election).only("id", "freeipa_username").order_by("freeipa_username", "id")
    )
    candidate_username_by_id = candidate_username_by_id_map(candidates)

    audit_qs = AuditLogEntry.objects.filter(election=election)
    can_manage_elections = request.user.has_perm(ASTRA_ADD_ELECTION)
    if not can_manage_elections:
        audit_qs = audit_qs.filter(is_public=True)

    timeline_items: list[AuditLogEntry | dict[str, object]] = []
    non_ballot_entries = list(
        audit_qs.exclude(event_type="ballot_submitted")
        .only("id", "timestamp", "event_type", "payload", "is_public")
        .order_by("timestamp", "id")
    )

    cumulative_elected_by_entry_id: dict[int, set[int]] = {}

    def _round_order_key(entry: AuditLogEntry) -> tuple[int, int, datetime.datetime, int]:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        round_obj = payload.get("round")
        iteration_obj = payload.get("iteration")
        if isinstance(round_obj, int):
            return (0, round_obj, entry.timestamp, entry.id)
        if isinstance(iteration_obj, int):
            return (1, iteration_obj, entry.timestamp, entry.id)
        return (2, 0, entry.timestamp, entry.id)

    cumulative_elected_ids: set[int] = set()
    tally_round_entries = [entry for entry in non_ballot_entries if entry.event_type == "tally_round"]
    for tally_entry in sorted(tally_round_entries, key=_round_order_key):
        payload = tally_entry.payload if isinstance(tally_entry.payload, dict) else {}
        elected_obj = payload.get("elected")
        if isinstance(elected_obj, list):
            for elected_id_obj in elected_obj:
                try:
                    cumulative_elected_ids.add(int(elected_id_obj))
                except (TypeError, ValueError):
                    continue
        cumulative_elected_by_entry_id[tally_entry.id] = set(cumulative_elected_ids)

    timeline_items.extend(non_ballot_entries)

    if can_manage_elections:
        # Group ballot submissions by day for managers to keep the timeline readable.
        ballot_qs = audit_qs.filter(event_type="ballot_submitted")
        for row in (
            ballot_qs.annotate(day=TruncDate("timestamp"))
            .values("day")
            .annotate(
                ballots_count=Count("id"),
                first_timestamp=Min("timestamp"),
                last_timestamp=Max("timestamp"),
            )
            .order_by("day")
        ):
            day = row.get("day")
            first_ts = row.get("first_timestamp")
            last_ts = row.get("last_timestamp")
            if not isinstance(day, datetime.date) or not isinstance(first_ts, datetime.datetime) or not isinstance(
                last_ts, datetime.datetime
            ):
                continue
            timeline_items.append(
                {
                    "timestamp": last_ts,
                    "event_type": "ballots_submitted_summary",
                    "payload": {},
                    "ballot_date": day.isoformat(),
                    "ballots_count": int(row.get("ballots_count") or 0),
                    "first_timestamp": first_ts,
                    "last_timestamp": last_ts,
                }
            )

    def _timeline_sort_key(item: AuditLogEntry | dict[str, object]) -> tuple[datetime.datetime, int]:
        if isinstance(item, dict):
            ts = item.get("timestamp")
            if isinstance(ts, datetime.datetime):
                return (ts, 0)
            return (datetime.datetime.min.replace(tzinfo=timezone.get_current_timezone()), 0)
        return (item.timestamp, item.id)

    # Sort newest-first to support "load older" navigation.
    timeline_items.sort(key=_timeline_sort_key, reverse=True)

    page_raw = str(request.GET.get("page") or "1").strip()
    try:
        page_number = int(page_raw)
    except ValueError:
        page_number = 1

    paginator = Paginator(timeline_items, 60)
    page_obj = paginator.get_page(page_number)

    base_url = reverse("election-audit-log", args=[election.id])

    newer_url = (
        build_url_for_page(
            base_url,
            query=request.GET,
            page_param="page",
            page_value=page_obj.previous_page_number(),
        )
        if page_obj.has_previous()
        else ""
    )
    older_url = (
        build_url_for_page(
            base_url,
            query=request.GET,
            page_param="page",
            page_value=page_obj.next_page_number(),
        )
        if page_obj.has_next()
        else ""
    )

    ballot_preview_by_date: dict[str, list[dict[str, object]]] = {}
    ballot_preview_limit = 50
    if can_manage_elections:
        preview_dates: list[datetime.date] = []
        for it in page_obj.object_list:
            if not isinstance(it, dict):
                continue
            if str(it.get("event_type") or "") != "ballots_submitted_summary":
                continue
            day_raw = str(it.get("ballot_date") or "").strip()
            if not day_raw:
                continue
            try:
                preview_dates.append(datetime.date.fromisoformat(day_raw))
            except ValueError:
                continue

        if preview_dates:
            ballot_qs = audit_qs.filter(event_type="ballot_submitted")
            for day in sorted(set(preview_dates)):
                rows = list(
                    ballot_qs.filter(timestamp__date=day)
                    .only("timestamp", "payload")
                    .order_by("timestamp", "id")[:ballot_preview_limit]
                )
                preview: list[dict[str, object]] = []
                for row in rows:
                    payload = row.payload if isinstance(row.payload, dict) else {}
                    ballot_hash = str(payload.get("ballot_hash") or "").strip()
                    supersedes_hash = str(payload.get("supersedes_ballot_hash") or "").strip()
                    preview.append(
                        {
                            "timestamp": row.timestamp,
                            "ballot_hash": ballot_hash,
                            "supersedes_ballot_hash": supersedes_hash,
                        }
                    )
                ballot_preview_by_date[day.isoformat()] = preview

    ballot_agg = Ballot.objects.for_election(election=election).final().aggregate(
        ballots=Count("id"),
        weight_total=Sum("weight"),
    )
    ballots_cast = int(ballot_agg.get("ballots") or 0)
    votes_cast = int(ballot_agg.get("weight_total") or 0)

    tally_result = election.tally_result or {}

    sankey_flows: list[dict[str, object]] = []
    sankey_elected_nodes: list[str] = []
    sankey_eliminated_nodes: list[str] = []
    if election.status == Election.Status.tallied:
        sankey_flows, sankey_elected_nodes, sankey_eliminated_nodes = build_sankey_flows(
            tally_result=tally_result,
            candidate_username_by_id=candidate_username_by_id,
            votes_cast=votes_cast,
        )

    def _icon_for_event(event_type: str) -> tuple[str, str]:
        match event_type:
            case "election_started":
                return ("fas fa-play", "bg-green")
            case "ballot_submitted":
                return ("fas fa-vote-yea", "bg-blue")
            case "ballots_submitted_summary":
                return ("fas fa-layer-group", "bg-blue")
            case "quorum_reached":
                return ("fas fa-check-circle", "bg-success")
            case "election_end_extended":
                return ("fas fa-calendar-plus", "bg-orange")
            case "election_closed":
                return ("fas fa-lock", "bg-orange")
            case "election_anonymized":
                return ("fas fa-user-secret", "bg-purple")
            case "tally_round":
                return ("fas fa-calculator", "bg-info")
            case "tally_completed":
                return ("fas fa-flag-checkered", "bg-success")
            case _:
                return ("fas fa-info-circle", "bg-secondary")

    def _title_for_event(event_type: str, payload: dict[str, object]) -> str:
        match event_type:
            case "election_started":
                return "Election started"
            case "ballot_submitted":
                return "Ballot submitted"
            case "ballots_submitted_summary":
                return "Ballots submitted"
            case "quorum_reached":
                return "Quorum reached"
            case "election_end_extended":
                return "Election end extended"
            case "election_closed":
                return "Election closed"
            case "election_anonymized":
                return "Election anonymized"
            case "tally_round":
                round_number = payload.get("round")
                iteration = payload.get("iteration")
                if isinstance(round_number, int) and isinstance(iteration, int):
                    return f"Tally round {round_number} (iteration {iteration})"
                if isinstance(round_number, int):
                    return f"Tally round {round_number}"
                if isinstance(iteration, int):
                    return f"Tally iteration {iteration}"
                return "Tally round"
            case "tally_completed":
                return "Tally completed"
            case _:
                return event_type.replace("_", " ")

    def _candidate_username(cid: int) -> str:
        username = candidate_username_by_id.get(cid)
        if username:
            return username
        return str(cid)

    events: list[dict[str, object]] = []
    jump_links: list[dict[str, str]] = []
    anchor_for_event_type = {
        "election_closed": "jump-election-closed",
        "tally_round": "jump-tally-rounds",
        "tally_completed": "jump-tally-completed",
    }
    anchor_labels = {
        "election_closed": "Election closed",
        "tally_round": "Tally rounds",
        "tally_completed": "Results",
    }
    anchors_added: set[str] = set()

    for item in page_obj.object_list:
        if isinstance(item, dict):
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            event_type = str(item.get("event_type") or "").strip() or "unknown"
            timestamp = item.get("timestamp")
            if not isinstance(timestamp, datetime.datetime):
                continue
            icon, icon_bg = _icon_for_event(event_type)
            event: dict[str, object] = {
                "timestamp": timestamp,
                "event_type": event_type,
                "title": _title_for_event(event_type, payload),
                "icon": icon,
                "icon_bg": icon_bg,
                "payload": payload,
            }
            event.update(item)

            if event_type == "ballots_submitted_summary":
                day = str(event.get("ballot_date") or "").strip()
                entries = ballot_preview_by_date.get(day, [])
                event["ballot_entries"] = entries
                try:
                    count = int(event.get("ballots_count") or 0)
                except (TypeError, ValueError):
                    count = 0
                event["ballots_preview_truncated"] = count > len(entries)
                event["ballots_preview_limit"] = ballot_preview_limit

            anchor = anchor_for_event_type.get(event_type)
            if anchor and anchor not in anchors_added:
                anchors_added.add(anchor)
                event["anchor"] = anchor
                jump_links.append({"anchor": anchor, "label": anchor_labels.get(event_type, event_type)})
            events.append(event)
            continue

        entry = item
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        event_type = str(entry.event_type or "").strip() or "unknown"
        icon, icon_bg = _icon_for_event(event_type)

        event: dict[str, object] = {
            "timestamp": entry.timestamp,
            "event_type": event_type,
            "title": _title_for_event(event_type, payload),
            "icon": icon,
            "icon_bg": icon_bg,
            "payload": payload,
        }

        anchor = anchor_for_event_type.get(event_type)
        if anchor and anchor not in anchors_added:
            anchors_added.add(anchor)
            event["anchor"] = anchor
            jump_links.append({"anchor": anchor, "label": anchor_labels.get(event_type, event_type)})

        if event_type == "tally_round":
            retained_totals_obj = payload.get("retained_totals")
            retention_factors_obj = payload.get("retention_factors")
            retained_totals: dict[int, str] = {}
            retention_factors: dict[int, str] = {}

            if isinstance(retained_totals_obj, dict):
                for k, v in retained_totals_obj.items():
                    try:
                        cid = int(k)
                    except (TypeError, ValueError):
                        continue
                    retained_totals[cid] = str(v)

            if isinstance(retention_factors_obj, dict):
                for k, v in retention_factors_obj.items():
                    try:
                        cid = int(k)
                    except (TypeError, ValueError):
                        continue
                    retention_factors[cid] = str(v)

            elected_ids_obj = payload.get("elected")
            per_round_elected_ids: set[int] = set()
            if isinstance(elected_ids_obj, list):
                for elected_id_obj in elected_ids_obj:
                    try:
                        per_round_elected_ids.add(int(elected_id_obj))
                    except (TypeError, ValueError):
                        continue

            elected_ids = cumulative_elected_by_entry_id.get(entry.id, set())
            eliminated_obj = payload.get("eliminated")
            eliminated_id = int(eliminated_obj) if isinstance(eliminated_obj, int) else None

            def _sort_key(item: tuple[int, str]) -> tuple[Decimal, str]:
                cid, retained_str = item
                try:
                    retained_val = Decimal(str(retained_str))
                except (InvalidOperation, ValueError):
                    retained_val = Decimal(0)
                return (retained_val, _candidate_username(cid).lower())

            round_rows: list[dict[str, object]] = []
            for cid, retained_str in sorted(retained_totals.items(), key=_sort_key, reverse=True):
                username = candidate_username_by_id.get(cid, "")
                profile_url = reverse("user-profile", args=[username]) if username else ""
                round_rows.append(
                    {
                        "candidate_id": cid,
                        "candidate_username": username,
                        "candidate_profile_url": profile_url,
                        "candidate_label": username or str(cid),
                        "retained_total": retained_str,
                        "retention_factor": retention_factors.get(cid, ""),
                        "is_elected": cid in elected_ids,
                        "is_eliminated": eliminated_id is not None and cid == eliminated_id,
                    }
                )

            event["round_rows"] = round_rows
            event["summary_text"] = str(payload.get("summary_text") or "").strip()
            event["audit_text"] = str(payload.get("audit_text") or "").strip()

        if event_type == "tally_completed":
            elected_obj = payload.get("elected")
            elected_ids = [int(x) for x in elected_obj] if isinstance(elected_obj, list) else []
            event["elected_users"] = _elected_candidate_display(
                elected_ids, candidate_username_by_id=candidate_username_by_id,
            )

        events.append(event)

    elected_ids, empty_seats = _tally_elected_ids(election)
    tally_elected_users = _elected_candidate_display(
        elected_ids, candidate_username_by_id=candidate_username_by_id,
    )

    return render(
        request,
        "core/election_audit_log.html",
        {
            "election": election,
            "can_manage_elections": can_manage_elections,
            "events": events,
            "jump_links": jump_links,
            "newer_url": newer_url,
            "older_url": older_url,
            "page_obj": page_obj,
            "candidates": candidates,
            "ballots_cast": ballots_cast,
            "votes_cast": votes_cast,
            "tally_result": tally_result,
            "quota": tally_result.get("quota"),
            "tally_elected_users": tally_elected_users,
            "empty_seats": empty_seats,
            "sankey_flows": sankey_flows,
            "sankey_elected_nodes": sankey_elected_nodes,
            "sankey_eliminated_nodes": sankey_eliminated_nodes,
        },
    )
