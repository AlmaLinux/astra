import json
import uuid
from typing import Any

from django.contrib.auth.decorators import user_passes_test
from django.core.cache import caches
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.elections_meek import tally_meek
from core.elections_sankey import build_sankey_flows
from core.views_utils import _normalize_str


def _safe_preview(value: Any, *, max_chars: int) -> Any:
    if value is None:
        return None

    try:
        if isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, sort_keys=True, default=str)
        else:
            text = str(value)
    except Exception:
        text = repr(value)

    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def _list_keys_from_backend(backend) -> list[str] | None:
    # LocMemCache exposes a per-process dict called _cache.
    internal = getattr(backend, "_cache", None)
    if isinstance(internal, dict):
        return sorted(str(k) for k in internal.keys())
    return None


@require_GET
@user_passes_test(lambda u: bool(u.is_superuser), login_url="/admin/login/")
def cache_debug_view(request):
    """Superuser-only cache inspection endpoint.

    This runs inside the live Django process, so it can see LocMemCache keys.
    """

    backend = caches["default"]
    backend_path = f"{backend.__class__.__module__}.{backend.__class__.__name__}"

    max_chars = _normalize_str(request.GET.get("max_chars", "4000"))
    try:
        max_chars_i = int(max_chars)
    except ValueError:
        max_chars_i = 4000
    max_chars_i = max(0, min(max_chars_i, 50000))

    prefix = _normalize_str(request.GET.get("prefix")) or None
    key = _normalize_str(request.GET.get("key")) or None

    keys = _list_keys_from_backend(backend)
    supports_key_listing = keys is not None

    if keys is None:
        keys = []

    if prefix:
        keys = [k for k in keys if k.startswith(prefix)]

    payload: dict[str, Any] = {
        "backend": backend_path,
        "supports_key_listing": supports_key_listing,
        "count": len(keys),
        "keys": keys,
        "known_freeipa_keys": [
            "freeipa_users_all",
            "freeipa_groups_all",
            "freeipa_user_<username>",
            "freeipa_group_<cn>",
        ],
    }

    if key:
        payload["key"] = key
        payload["value_preview"] = _safe_preview(backend.get(key), max_chars=max_chars_i)

    return JsonResponse(payload)


def _wikipedia_example() -> tuple[int, list[dict[str, object]], list[dict[str, object]], int, dict[int, str], list[dict[str, object]]]:
    candidates = [
        {"id": 10, "name": "Orange", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
        {"id": 11, "name": "Pear", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
        {"id": 12, "name": "Strawberry", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
        {"id": 13, "name": "Cake", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000013")},
        {"id": 14, "name": "Chocolate", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000014")},
        {"id": 15, "name": "Hamburger", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000015")},
        {"id": 16, "name": "Chicken", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000016")},
    ]
    ballots = [
        {"weight": 3, "ranking": [10, 11]},
        {"weight": 8, "ranking": [11, 12, 13]},
        {"weight": 1, "ranking": [12, 10, 11]},
        {"weight": 3, "ranking": [13, 14]},
        {"weight": 1, "ranking": [14, 13, 15]},
        {"weight": 4, "ranking": [15, 16]},
        {"weight": 3, "ranking": [16, 14, 15]},
    ]
    votes_cast = sum(int(ballot.get("weight") or 0) for ballot in ballots)
    candidate_name_by_id = {int(c["id"]): str(c.get("name") or "").strip() for c in candidates}
    return 3, candidates, ballots, votes_cast, candidate_name_by_id, []

# def _exclusion_example() -> tuple[int, list[dict[str, object]], list[dict[str, object]], int, dict[int, str], list[dict[str, object]]]:
#     # This is a modified version of the Wikipedia example with an exclusion group added.
#     seats, candidates, ballots, votes_cast, candidate_name_by_id, exclusions = _wikipedia_example()
#     exclusions = [
#         {"public_id": 1, "name": "Healthy food", "max_elected": 1, "candidate_ids": [10, 11, 12]},
#     ]
#     return seats, candidates, ballots, votes_cast, candidate_name_by_id, exclusions

@require_GET
@user_passes_test(lambda u: bool(u.is_superuser), login_url="/admin/login/")
def sankey_debug_view(request: HttpRequest) -> HttpResponse:
    example = _normalize_str(request.GET.get("example") or "wikipedia") or "wikipedia"
    if example != "wikipedia":
        return HttpResponseBadRequest("Unknown sankey example")

    seats, candidates, ballots, votes_cast, candidate_name_by_id, exclusions = _wikipedia_example()
    tally_result = tally_meek(seats=seats, ballots=ballots, candidates=candidates, exclusion_groups=exclusions)
    sankey_flows, elected_nodes, eliminated_nodes = build_sankey_flows(
        tally_result=tally_result,
        candidate_username_by_id=candidate_name_by_id,
        votes_cast=votes_cast,
    )

    round_rows: list[dict[str, object]] = []
    rounds_obj = tally_result.get("rounds")
    if isinstance(rounds_obj, list):
        for idx, round_data in enumerate(rounds_obj, start=1):
            if not isinstance(round_data, dict):
                continue
            round_label = f"Tally round {idx}"
            summary_text = str(round_data.get("summary_text") or "").strip()
            audit_text = str(round_data.get("audit_text") or "").strip()

            retained_totals_obj = round_data.get("retained_totals")
            retention_factors_obj = round_data.get("retention_factors")
            retained_totals = retained_totals_obj if isinstance(retained_totals_obj, dict) else {}
            retention_factors = retention_factors_obj if isinstance(retention_factors_obj, dict) else {}

            elected_obj = round_data.get("elected")
            elected_ids = {int(x) for x in elected_obj} if isinstance(elected_obj, list) else set()
            eliminated_obj = round_data.get("eliminated")
            eliminated_id = eliminated_obj if isinstance(eliminated_obj, int) else None

            rows: list[dict[str, object]] = []
            for cid_raw, retained in retained_totals.items():
                try:
                    cid = int(cid_raw)
                except (TypeError, ValueError):
                    continue
                label = candidate_name_by_id.get(cid, "") or f"Candidate {cid}"
                rows.append(
                    {
                        "candidate_label": label,
                        "retained_total": retained,
                        "retention_factor": retention_factors.get(str(cid), retention_factors.get(cid, "")),
                        "is_elected": cid in elected_ids,
                        "is_eliminated": eliminated_id is not None and cid == eliminated_id,
                    }
                )

            rows.sort(key=lambda row: str(row.get("candidate_label") or "").casefold())

            round_rows.append(
                {
                    "round_label": round_label,
                    "summary_text": summary_text,
                    "audit_text": audit_text,
                    "rows": rows,
                }
            )

    elected_labels: list[str] = []
    elected_obj = tally_result.get("elected")
    if isinstance(elected_obj, list):
        for cid_obj in elected_obj:
            try:
                cid = int(cid_obj)
            except (TypeError, ValueError):
                continue
            label = candidate_name_by_id.get(cid, "")
            if label:
                elected_labels.append(label)

    return render(
        request,
        "core/debug_sankey.html",
        {
            "example": "wikipedia",
            "example_label": "Wikipedia example",
            "ballots": ballots,
            "candidates": candidates,
            "votes_cast": votes_cast,
            "sankey_flows": sankey_flows,
            "sankey_elected_nodes": elected_nodes,
            "sankey_eliminated_nodes": eliminated_nodes,
            "debug_rounds": round_rows,
            "tally_elected_labels": elected_labels,
            "tally_result": tally_result,
        },
    )
