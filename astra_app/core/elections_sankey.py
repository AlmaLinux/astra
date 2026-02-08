from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


def _round_label(idx: int, round_data: Mapping[str, object]) -> str:
    iteration_obj = round_data.get("iteration")
    iteration = iteration_obj if isinstance(iteration_obj, int) else idx
    return f"Round {iteration}"


def _candidate_label(cid: int, candidate_username_by_id: Mapping[int, str]) -> str:
    candidate_label = candidate_username_by_id.get(cid, "")
    if candidate_label:
        return candidate_label
    return f"Candidate {cid}"


def _node_id(round_label: str, candidate_label: str) -> str:
    return f"{round_label} · {candidate_label}"


def _round_flow(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001")))


def build_sankey_flows(
    *,
    tally_result: Mapping[str, object],
    candidate_username_by_id: Mapping[int, str],
    votes_cast: int,
) -> tuple[list[dict[str, object]], list[str], list[str]]:
    sankey_flows: list[dict[str, object]] = []
    elected_nodes: list[str] = []
    eliminated_nodes: list[str] = []
    rounds_obj = tally_result.get("rounds")
    if not isinstance(rounds_obj, list):
        return sankey_flows, elected_nodes, eliminated_nodes

    round_labels: list[str] = []
    round_totals: list[dict[int, Decimal]] = []
    for idx, round_data in enumerate(rounds_obj, start=1):
        if not isinstance(round_data, dict):
            continue
        round_label = _round_label(idx, round_data)
        round_labels.append(round_label)

        retained_totals_obj = round_data.get("retained_totals")
        if not isinstance(retained_totals_obj, dict):
            round_totals.append({})
            continue

        totals: dict[int, Decimal] = {}
        for cid_raw, total_raw in retained_totals_obj.items():
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError):
                continue
            try:
                total_val = Decimal(str(total_raw))
            except (InvalidOperation, ValueError):
                continue
            if total_val <= 0:
                continue
            totals[cid] = total_val
        round_totals.append(totals)

    if not round_labels:
        return sankey_flows, elected_nodes, eliminated_nodes

    first_totals = round_totals[0]
    scale = Decimal(1)
    total_first = sum(first_totals.values(), start=Decimal(0))
    if votes_cast > 0 and total_first > 0:
        scale = Decimal(votes_cast) / total_first
    for cid, total_val in first_totals.items():
        candidate_label = _candidate_label(cid, candidate_username_by_id)
        sankey_flows.append(
            {
                "from": "Voters",
                "to": _node_id(round_labels[0], candidate_label),
                "flow": _round_flow(total_val * scale),
            }
        )

    elected_by_round: list[set[int]] = []
    elected_so_far: set[int] = set()

    eliminated_by_round: list[int | None] = []

    for round_data in rounds_obj:
        if not isinstance(round_data, dict):
            elected_by_round.append(set())
            eliminated_by_round.append(None)
            continue
        elected_obj = round_data.get("elected")
        elected_ids = {int(x) for x in elected_obj} if isinstance(elected_obj, list) else set()
        elected_so_far |= elected_ids
        elected_by_round.append(set(elected_so_far))

        eliminated_obj = round_data.get("eliminated")
        try:
            eliminated_id = int(eliminated_obj) if eliminated_obj is not None else None
        except (TypeError, ValueError):
            eliminated_id = None
        eliminated_by_round.append(eliminated_id)

    for round_idx in range(len(round_totals) - 1):
        prev_totals = round_totals[round_idx]
        next_totals = round_totals[round_idx + 1]
        prev_label = round_labels[round_idx]
        next_label = round_labels[round_idx + 1]

        candidate_ids = set(prev_totals.keys()) | set(next_totals.keys())
        losses: dict[int, Decimal] = {}
        gains: dict[int, Decimal] = {}

        for cid in candidate_ids:
            prev_val = prev_totals.get(cid, Decimal(0))
            next_val = next_totals.get(cid, Decimal(0))
            shared = prev_val if prev_val <= next_val else next_val
            if shared > 0:
                candidate_label = _candidate_label(cid, candidate_username_by_id)
                sankey_flows.append(
                    {
                        "from": _node_id(prev_label, candidate_label),
                        "to": _node_id(next_label, candidate_label),
                        "flow": _round_flow(shared),
                    }
                )

            if prev_val > shared:
                losses[cid] = prev_val - shared
            if next_val > shared:
                gains[cid] = next_val - shared

        total_loss = sum(losses.values(), start=Decimal(0))
        total_gain = sum(gains.values(), start=Decimal(0))
        if total_loss > 0 and total_gain > 0:
            for loser_cid, loss_val in losses.items():
                from_label = _candidate_label(loser_cid, candidate_username_by_id)
                from_node = _node_id(prev_label, from_label)
                for gainer_cid, gain_val in gains.items():
                    ratio = gain_val / total_gain
                    flow_val = loss_val * ratio
                    if flow_val <= 0:
                        continue
                    to_label = _candidate_label(gainer_cid, candidate_username_by_id)
                    sankey_flows.append(
                        {
                            "from": from_node,
                            "to": _node_id(next_label, to_label),
                            "flow": _round_flow(flow_val),
                        }
                    )

    for round_idx, elected_ids in enumerate(elected_by_round):
        if not elected_ids:
            continue
        round_label = round_labels[round_idx]
        for cid in sorted(elected_ids):
            label = _candidate_label(cid, candidate_username_by_id)
            if not label:
                continue
            elected_nodes.append(_node_id(round_label, label))

    for round_idx, cid in enumerate(eliminated_by_round):
        if cid is None:
            continue
        label = _candidate_label(cid, candidate_username_by_id)
        if not label:
            continue
        round_label = round_labels[round_idx]
        eliminated_nodes.append(_node_id(round_label, label))

    return sankey_flows, elected_nodes, eliminated_nodes
