from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, localcontext


@dataclass(frozen=True, slots=True)
class _Candidate:
    id: int
    name: str
    tiebreak_uuid: str


@dataclass(frozen=True, slots=True)
class _ExclusionGroup:
    public_id: str
    max_elected: int
    name: str
    candidate_ids: frozenset[int]


def _decimal(value: int | str | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _ballot_ranking(ballot: Mapping[str, object]) -> list[int]:
    ranking = ballot.get("ranking")
    if not isinstance(ranking, list):
        return []
    result: list[int] = []
    for x in ranking:
        try:
            cid = int(x)
            # Reasonable bounds: candidate IDs should be positive and not absurdly large
            if 0 <= cid <= 1_000_000:
                result.append(cid)
        except (ValueError, TypeError, OverflowError):
            # Skip malformed candidate IDs
            continue
    return result


def _ballot_weight(ballot: Mapping[str, object]) -> Decimal:
    try:
        weight_val = int(ballot.get("weight") or 0)
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError("invalid ballot weight") from e

    # Elections should never produce absurdly large or negative ballot weights.
    # If this happens, it indicates a corrupted DB row or a bug upstream.
    if weight_val < 0 or weight_val > 1_000_000:
        raise ValueError(f"ballot weight {weight_val} out of valid range [0, 1000000]")

    return _decimal(weight_val)


def _distribute_votes(
    *,
    ballots: Iterable[Mapping[str, object]],
    retention: Mapping[int, Decimal],
    continuing_ids: frozenset[int],
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    incoming: dict[int, Decimal] = {cid: Decimal(0) for cid in continuing_ids}
    retained: dict[int, Decimal] = {cid: Decimal(0) for cid in continuing_ids}

    for ballot in ballots:
        remaining = _ballot_weight(ballot)
        if remaining <= 0:
            continue

        for cid in _ballot_ranking(ballot):
            if remaining <= 0:
                break
            if cid not in continuing_ids:
                continue

            r = retention[cid]
            if r <= 0:
                continue

            incoming[cid] += remaining
            portion = remaining * r
            if portion:
                retained[cid] += portion
                remaining -= portion

    return incoming, retained


def _first_preferences(*, ballots: Iterable[Mapping[str, object]], continuing_ids: frozenset[int]) -> dict[int, Decimal]:
    first: dict[int, Decimal] = {cid: Decimal(0) for cid in continuing_ids}
    for ballot in ballots:
        w = _ballot_weight(ballot)
        if w <= 0:
            continue
        for cid in _ballot_ranking(ballot):
            if cid in continuing_ids:
                first[cid] += w
                break
    return first

def _format_list(items: Iterable[str], joiner: str = "and") -> str:
    items_list = list(items)
    if not items_list:
        return "none"
    if len(items_list) == 1:
        return items_list[0]
    if len(items_list) == 2:
        return f"{items_list[0]} {joiner} {items_list[1]}"
    return ", ".join(items_list[:-1]) + f", {joiner} {items_list[-1]}"

def _format_candidate_list(
    candidate_ids: Iterable[int],
    *,
    candidate_name_by_id: Mapping[int, str],
) -> str:
    named: list[str] = []
    unnamed_count = 0
    for cid in candidate_ids:
        name = candidate_name_by_id.get(cid, "").strip()
        if name:
            named.append(name)
        else:
            unnamed_count += 1

    if not named and unnamed_count == 0:
        return "none"

    items = list(named)
    if unnamed_count == 1:
        items.append("an unnamed candidate")
    elif unnamed_count > 1:
        items.append(f"{unnamed_count} unnamed candidates")

    return _format_list(items, joiner="and")


def generate_meek_round_explanations(
    round_data: Mapping[str, object],
    *,
    quota: Decimal,
    candidate_name_by_id: Mapping[int, str] | None = None,
) -> dict[str, str]:
    """Generate public-facing explanations for a single Meek STV iteration.

    This is intended for governance / organizational elections:
    - Accurate and deterministic (same input -> same text)
    - Neutral (describes outcomes and constraints without editorializing)
    - Human-friendly (avoids internal implementation details)

    Input is one iteration dict from the audit log / tally output.
    Output includes:
    - audit_text: structured prose suitable for public release
    - summary_text: a compact single-sentence summary
    """
    names = candidate_name_by_id or {}

    iteration_obj = round_data.get("iteration")
    iteration = iteration_obj if isinstance(iteration_obj, int) else 0

    elected_obj = round_data.get("elected")
    elected: list[int] = [int(x) for x in elected_obj] if isinstance(elected_obj, list) else []

    fill_obj = round_data.get("elected_to_fill_remaining_seats")
    elected_to_fill_remaining_seats: list[int] = [int(x) for x in fill_obj] if isinstance(fill_obj, list) else []

    quota_reached_obj = round_data.get("quota_reached")
    quota_reached: list[int] = [int(x) for x in quota_reached_obj] if isinstance(quota_reached_obj, list) else []

    eliminated_obj = round_data.get("eliminated")
    eliminated: int | None = eliminated_obj if isinstance(eliminated_obj, int) else None

    forced_exclusions_obj = round_data.get("forced_exclusions")
    forced_exclusions: list[Mapping[str, object]] = (
        [x for x in forced_exclusions_obj if isinstance(x, Mapping)] if isinstance(forced_exclusions_obj, list) else []
    )

    numerically_converged = bool(round_data.get("numerically_converged"))
    count_complete = bool(round_data.get("count_complete"))
    seats_obj = round_data.get("seats")
    seats = int(seats_obj) if isinstance(seats_obj, int) else 0
    elected_total_obj = round_data.get("elected_total")
    elected_total = int(elected_total_obj) if isinstance(elected_total_obj, int) else 0

    tie_breaks_obj = round_data.get("tie_breaks")
    tie_breaks: list[Mapping[str, object]] = (
        [x for x in tie_breaks_obj if isinstance(x, Mapping)] if isinstance(tie_breaks_obj, list) else []
    )

    eligible_obj = round_data.get("eligible_candidates")
    eligible_candidates: list[int] = [int(x) for x in eligible_obj] if isinstance(eligible_obj, list) else []
    eligible_candidates.sort(key=lambda cid: (names.get(cid, "").casefold(), cid))

    quota_reached_str = _format_candidate_list(quota_reached, candidate_name_by_id=names)
    elected_str = _format_candidate_list(elected, candidate_name_by_id=names)

    elected_by_rule = [cid for cid in elected_to_fill_remaining_seats if cid in elected]
    elected_by_quota = [cid for cid in elected if cid in quota_reached and cid not in elected_by_rule]
    elected_other = [cid for cid in elected if cid not in elected_by_rule and cid not in elected_by_quota]
    quota_reached_not_elected = [cid for cid in quota_reached if cid not in elected]

    forced_excluded_ids = {
        int(fx["candidate_id"]) for fx in forced_exclusions if isinstance(fx.get("candidate_id"), int)
    }
    quota_reached_not_elected_without_forced = [
        cid for cid in quota_reached_not_elected if cid not in forced_excluded_ids
    ]

    audit_parts: list[str] = []

    remaining_seats = max(seats - elected_total, 0) if seats > 0 else 0
    seats_filled = bool(seats and elected_total >= seats)

    elected_total_before_iteration = max(elected_total - len(elected), 0)
    remaining_seats_before_iteration = (
        max(seats - elected_total_before_iteration, 0) if seats > 0 else 0
    )

    def _plural_s(n: int) -> str:
        return "" if n == 1 else "s"

    def _was_were(n: int) -> str:
        return "was" if n == 1 else "were"

    elimination_emitted = False

    for tie_break in tie_breaks:
        kind = str(tie_break.get("type") or "").strip()
        candidate_ids_obj = tie_break.get("candidate_ids")
        tied_candidate_ids: list[int] = (
            [int(x) for x in candidate_ids_obj] if isinstance(candidate_ids_obj, list) else []
        )
        if not tied_candidate_ids:
            continue

        tied_str = _format_candidate_list(tied_candidate_ids, candidate_name_by_id=names)
        tie_context = (
            "for elimination" if kind == "elimination" else "when determining a deterministic processing order"
        )
        paragraph = [
            f"Candidates {tied_str} were tied {tie_context}. "
            "The election uses predefined deterministic tie-breaking rules applied in sequence so outcomes are repeatable."
        ]

        rule_trace_obj = tie_break.get("rule_trace")
        rule_trace: list[Mapping[str, object]] = rule_trace_obj if isinstance(rule_trace_obj, list) else []

        failed_rules = [str(rule.get("title", "unnamed rule")) for rule in rule_trace if rule.get("result") == "tied"]
        successful_rule = [rule for rule in rule_trace if rule.get("result") == "resolved"][0]

        if failed_rules:
            paragraph.append(f"No distinction could be made based on {_format_list(failed_rules, joiner='or')}.")

        resolved_by = (
            "a fixed candidate ordering identifier"
            if successful_rule.get("rule") == 4
            else str(successful_rule.get("title", "a predefined rule"))
        )
        paragraph.append(f"The tie was resolved using {resolved_by}.")

        if kind == "elimination":
            selected_obj = tie_break.get("selected")
            selected = int(selected_obj) if isinstance(selected_obj, int) else None
            if selected is not None:
                selected_str = _format_candidate_list([selected], candidate_name_by_id=names)
                paragraph.append(f"Under these rules, candidate {selected_str} was selected for elimination.")
        else:
            ordered_obj = tie_break.get("ordered")
            ordered: list[int] = [int(x) for x in ordered_obj] if isinstance(ordered_obj, list) else []
            if ordered:
                paragraph.append(
                    f"Under these rules, candidate {_format_candidate_list([ordered[0]], candidate_name_by_id=names)} was ordered ahead of candidate{_plural_s(max(len(ordered) - 1, 1))} {_format_candidate_list(ordered[1:], candidate_name_by_id=names)}."
                )

        closing_sentence = (
            "This deterministic selection resolves the tie."
            if kind == "elimination"
            else "This tie-break outcome is used only to decide which action to take first and does not imply a difference in vote totals."
        )
        paragraph.append(closing_sentence)

        audit_parts.append(" ".join(paragraph))
        audit_parts.append("")

    if quota_reached_not_elected_without_forced:
        audit_parts.append(
            f"During this iteration, candidate{_plural_s(len(quota_reached))} {quota_reached_str} reached the election quota ({quota:.4f}), but not all could be elected under the election rules."
        )
        audit_parts.append("")

    if eliminated is not None and elected_by_rule:
        eliminated_str = _format_candidate_list([eliminated], candidate_name_by_id=names)

        elimination_tie_break = any(
            str(tb.get("type") or "").strip() == "elimination" and tb.get("selected") == eliminated
            for tb in tie_breaks
            if isinstance(tb, Mapping)
        )

        elimination_clause = " (tied for the lowest vote total)" if elimination_tie_break else ""
        eliminated_reason = "had the lowest vote total and " if not elimination_tie_break else ""

        transfer_sentence = (
            "Since the count is complete, no further redistribution is needed to determine the final outcome."
            if count_complete
            else "Their vote value will transfer to remaining eligible candidates according to voter preferences under the counting method."
        )
        audit_parts.append(
            f"Candidate {eliminated_str} {eliminated_reason}was eliminated from the count{elimination_clause}. {transfer_sentence}"
        )
        audit_parts.append("")
        elimination_emitted = True

    if elected:
        election_sentences: list[str] = []

        if elected_by_quota:
            elected_by_quota_str = _format_candidate_list(elected_by_quota, candidate_name_by_id=names)
            if quota_reached_not_elected:
                election_sentences.append(
                    f"Candidate{_plural_s(len(elected_by_quota))} {elected_by_quota_str} {_was_were(len(elected_by_quota))} elected after meeting the election quota ({quota:.4f})."
                )

                if quota_reached_not_elected_without_forced:
                    quota_not_elected_str = _format_candidate_list(
                        quota_reached_not_elected_without_forced,
                        candidate_name_by_id=names,
                    )
                    election_sentences.append(
                        f"Candidate{_plural_s(len(quota_reached_not_elected_without_forced))} {quota_not_elected_str} also reached the quota but could not be elected under the election rules."
                    )

                seat_limited_quota = (
                    not forced_exclusions
                    and remaining_seats_before_iteration > 0
                    and len(quota_reached) > remaining_seats_before_iteration
                )
                if seat_limited_quota:
                    election_sentences.append(
                        f"More candidates reached the quota than there were remaining seat{_plural_s(remaining_seats_before_iteration)}, so only {remaining_seats_before_iteration} could be elected this iteration."
                    )
                    if any(str(tb.get("type") or "").strip() == "election_order" for tb in tie_breaks):
                        election_sentences.append(
                            "When needed, deterministic tie-breaking rules decide which quota-meeting candidates are elected."
                        )
            else:
                election_sentences.append(
                    f"Candidate{_plural_s(len(elected_by_quota))} {elected_by_quota_str} {_was_were(len(elected_by_quota))} elected after meeting the election quota ({quota:.4f})."
                )

            election_sentences.append(
                "Under Meek STV, an elected candidate keeps only enough vote to reach the quota; any excess vote value is transferred to next preferences."
            )

        if elected_by_rule:
            elected_by_rule_str = _format_candidate_list(elected_by_rule, candidate_name_by_id=names)
            election_sentences.append(
                f"{'After this elimination, the' if eliminated is not None else 'The'} remaining eligible candidate{_plural_s(len(elected_by_rule))} exactly filled the remaining seat{_plural_s(len(elected_by_rule))}, so candidate{_plural_s(len(elected_by_rule))} {elected_by_rule_str} {_was_were(len(elected_by_rule))} elected under the election rules."
            )

        if elected_other:
            elected_other_str = _format_candidate_list(elected_other, candidate_name_by_id=names)
            election_sentences.append(
                f"Candidate{_plural_s(len(elected_other))} {elected_other_str} {_was_were(len(elected_other))} elected."
            )

        if election_sentences:
            audit_parts.append(" ".join(election_sentences))
            audit_parts.append("")

    if forced_exclusions:
        # Group forced exclusions by group name (fallback to public_id).
        excluded_ids_by_group: dict[str, list[int]] = {}
        triggered_by_by_group: dict[str, int] = {}
        for fx in forced_exclusions:
            group_name = str(fx.get("group_name") or "").strip()
            if not group_name:
                group_name = str(fx.get("group_public_id") or "").strip()

            candidate_id = fx.get("candidate_id")
            if not isinstance(candidate_id, int):
                continue

            excluded_ids_by_group.setdefault(group_name, []).append(candidate_id)

            triggered_by_obj = fx.get("triggered_by")
            if isinstance(triggered_by_obj, int) and group_name not in triggered_by_by_group:
                triggered_by_by_group[group_name] = int(triggered_by_obj)

        if excluded_ids_by_group:
            for group_name, excluded_ids in excluded_ids_by_group.items():
                # Safety: do not describe any elected candidate as excluded.
                excluded_ids = [cid for cid in excluded_ids if cid not in elected]
                excluded_reached_quota = [cid for cid in excluded_ids if cid in quota_reached]

                triggered_by = triggered_by_by_group.get(group_name)
                paragraph = []
                if triggered_by is not None:
                    trigger_name = _format_candidate_list([triggered_by], candidate_name_by_id=names)
                    paragraph.append(
                        f"Because candidate {trigger_name} was elected, the exclusion group constraint for \"{group_name}\" reached its maximum, so no additional candidates from that group could be elected."
                    )
                else:
                    paragraph.append(
                        f"Because an exclusion group constraint for \"{group_name}\" reached its maximum, no additional candidates from that group could be elected."
                    )

                if excluded_reached_quota:
                    excluded_str = _format_candidate_list(excluded_reached_quota, candidate_name_by_id=names)
                    verb = "was" if len(excluded_reached_quota) == 1 else "were"
                    paragraph.append(
                        f"As a result, candidate {excluded_str} could not be elected despite meeting the quota and {verb} removed from further consideration. "
                        "This is rule-based and not a vote comparison."
                    )

                    if not count_complete and eligible_candidates:
                        who = "candidate" if len(excluded_reached_quota) == 1 else "candidates"
                        paragraph.append(
                            f"Any vote value currently assigned to {who} {excluded_str} will transfer to remaining eligible candidates according to voter preferences and current retention factors."
                        )
                    elif count_complete:
                        paragraph.append("Since the count is complete, this exclusion does not affect the final outcome.")
                else:
                    excluded_str = _format_candidate_list(excluded_ids, candidate_name_by_id=names)
                    paragraph.append(
                        f"As a result, candidate {excluded_str} was removed from further consideration. "
                        "This is rule-based and not a vote comparison."
                    )

                    if not count_complete and eligible_candidates:
                        who = "candidate" if len(excluded_ids) == 1 else "candidates"
                        paragraph.append(
                            f"Any vote value currently assigned to {who} {excluded_str} will transfer to remaining eligible candidates according to voter preferences and current retention factors."
                        )
                    elif count_complete:
                        paragraph.append("Since the count is complete, this exclusion does not affect the final outcome.")

                audit_parts.append(" ".join(paragraph))
                audit_parts.append("")

    if eliminated is not None and not elimination_emitted:
        eliminated_str = _format_candidate_list([eliminated], candidate_name_by_id=names)

        elimination_tie_break = any(
            str(tb.get("type") or "").strip() == "elimination" and tb.get("selected") == eliminated
            for tb in tie_breaks
            if isinstance(tb, Mapping)
        )

        elimination_clause = " (tied for the lowest vote total)" if elimination_tie_break else ""
        eliminated_reason = "had the lowest vote total and " if not elimination_tie_break else ""

        transfer_sentence = (
            "Since the count is complete, no further redistribution is needed to determine the final outcome."
            if count_complete
            else "Their vote value will transfer to remaining eligible candidates according to voter preferences under the counting method."
        )
        audit_parts.append(
            f"Candidate {eliminated_str} {eliminated_reason}was eliminated from the count{elimination_clause}. {transfer_sentence}"
        )
        audit_parts.append("")

    has_iteration_events = bool(elected or eliminated is not None or forced_exclusions)

    if eligible_candidates and remaining_seats > 0 and not count_complete and not numerically_converged and has_iteration_events:
        audit_parts.append("Vote transfers will be recalculated in subsequent iterations.")
        audit_parts.append("")
    elif remaining_seats > 0 and not eligible_candidates and not count_complete:
        audit_parts.append("No candidates remain eligible.")
        audit_parts.append("")

    # Guardrails:
    # - Never equate numerical convergence with count completion.
    # - Never claim finality unless `count_complete` is true.
    if count_complete:
        if seats_filled:
            audit_parts.append("All available seats have been filled. Final results are now determined.")
        elif eligible_candidates and len(eligible_candidates) == remaining_seats and remaining_seats > 0:
            audit_parts.append(
                "The remaining eligible candidates exactly fill the remaining seats, so the outcome is determined under the election rules."
            )
        elif not eligible_candidates and remaining_seats > 0:
            audit_parts.append(
                f"No candidates remain eligible for the remaining seats, so no further elections or eliminations are possible under the election rules. {remaining_seats} seat{_plural_s(remaining_seats)} remain{'s' if remaining_seats == 1 else ''} vacant."
            )
        else:
            audit_parts.append("The outcome is determined under the election rules. Final results are now determined.")
    else:
        if numerically_converged and eliminated is None and not elected and not forced_exclusions:
            audit_parts.append("Vote transfers have stabilized. Further counting steps are still required.")
        elif not numerically_converged and eliminated is None and not elected and not forced_exclusions:
            audit_parts.append(
                "Vote transfers are still stabilizing. Further iterations are required to determine the final outcome."
            )
        else:
            audit_parts.append("The count is not yet complete. Further iterations are required to determine the final outcome.")

    audit_text = "\n".join(audit_parts).strip() + "\n"

    summary_bits: list[str] = []
    if tie_breaks:
        summary_bits.append("tie resolved deterministically")
    if elected:
        summary_bits.append(f"elected {elected_str}")
    if elected_to_fill_remaining_seats:
        summary_bits.append("filled remaining seats by rule")
    if eliminated is not None:
        summary_bits.append(f"eliminated {_format_candidate_list([eliminated], candidate_name_by_id=names)}")
    if count_complete:
        summary_bits.append("count complete")
    elif numerically_converged and eliminated is None and not elected and not forced_exclusions:
        summary_bits.append("vote transfers stabilized")
    else:
        summary_bits.append("further iterations required")
    summary_text = f"Iteration {iteration}: " + "; ".join(summary_bits) + "."

    return {"audit_text": audit_text, "summary_text": summary_text}


def tally_meek(
    *,
    ballots: list[dict[str, object]],
    candidates: list[dict[str, object]],
    seats: int,
    exclusion_groups: list[dict[str, object]] | None = None,
    epsilon: Decimal = Decimal("1e-28"),
    max_iterations: int = 200,
) -> dict[str, object]:
    """Tally an STV election using Meek STV.

    Design goals:
    - Deterministic: tie-breaks are fully specified and stable.
    - Auditable: the returned rounds include per-iteration retained totals and retention factors.
    - Privacy-preserving: operates on anonymous ballots and candidate IDs only.

    Notes:
    - Quota is the Meek quota: [1 + T/(S+1)].
    - Ballots are distributed fractionally using per-candidate retention factors.
    - Elected candidates remain in circulation with retention adjusted towards quota.
    - If no new elections occur after convergence, the lowest candidate is eliminated.
    - Exclusion groups force-exclude candidates once a group reaches its max elected.
    """

    # Input validation to prevent crashes and DoS attacks
    if seats <= 0:
        raise ValueError("seats must be positive")
    if seats > 10_000:
        raise ValueError("seats must not exceed 10,000")
    if not isinstance(ballots, list):
        raise TypeError("ballots must be a list")
    if len(ballots) > 1_000_000:
        raise ValueError("ballot count must not exceed 1,000,000")
    if not isinstance(candidates, list):
        raise TypeError("candidates must be a list")
    if len(candidates) == 0:
        raise ValueError("must have at least one candidate")
    if len(candidates) > 10_000:
        raise ValueError("candidate count must not exceed 10,000")
    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    if max_iterations < 1 or max_iterations > 1000:
        raise ValueError("max_iterations must be between 1 and 1000")

    parsed_candidates: list[_Candidate] = []
    for c in candidates:
        if not isinstance(c, dict):
            raise TypeError("each candidate must be a dict")
        if "id" not in c:
            raise ValueError("candidate missing required 'id' field")
        try:
            cid = int(c["id"])
            if cid < 0 or cid > 1_000_000:
                raise ValueError(f"candidate id {cid} out of valid range [0, 1000000]")
        except (ValueError, TypeError, OverflowError) as e:
            raise ValueError(f"invalid candidate id: {e}") from e

        name = str(c.get("name") or "")
        tiebreak_uuid = str(c.get("tiebreak_uuid") or "")

        # Validate tiebreak_uuid is present and reasonable
        if not tiebreak_uuid or len(tiebreak_uuid) > 200:
            raise ValueError(f"candidate {cid} has invalid or missing tiebreak_uuid")

        parsed_candidates.append(_Candidate(id=cid, name=name, tiebreak_uuid=tiebreak_uuid))

    candidate_name_by_id: dict[int, str] = {c.id: c.name for c in parsed_candidates if c.name}

    all_candidate_ids = frozenset(c.id for c in parsed_candidates)

    groups: list[_ExclusionGroup] = []
    for g in exclusion_groups or []:
        if not isinstance(g, dict):
            raise TypeError("each exclusion group must be a dict")

        try:
            max_elected = int(g.get("max_elected") or 0)
            if max_elected < 0 or max_elected > seats:
                raise ValueError(f"exclusion group max_elected {max_elected} out of valid range")
        except (ValueError, TypeError, OverflowError) as e:
            raise ValueError(f"invalid exclusion group max_elected: {e}") from e

        # Safely parse candidate IDs
        group_candidate_ids: set[int] = set()
        for x in (g.get("candidate_ids") or []):
            try:
                cid = int(x)
                if 0 <= cid <= 1_000_000:
                    group_candidate_ids.add(cid)
            except (ValueError, TypeError, OverflowError):
                continue

        groups.append(
            _ExclusionGroup(
                public_id=str(g.get("public_id") or ""),
                name=str(g.get("name") or ""),
                max_elected=max_elected,
                candidate_ids=frozenset(group_candidate_ids),
            )
        )

    with localcontext() as ctx:
        ctx.prec = 80

        total_weight = sum((_ballot_weight(b) for b in ballots), start=Decimal(0))
        quota = (total_weight / Decimal(seats + 1)).to_integral_value(rounding=ROUND_DOWN) + Decimal(1)

        retention: dict[int, Decimal] = {cid: Decimal(1) for cid in all_candidate_ids}
        elected: list[int] = []
        eliminated: list[int] = []
        forced_excluded: list[int] = []

        continuing_ids: set[int] = set(all_candidate_ids)
        first_pref = _first_preferences(ballots=ballots, continuing_ids=frozenset(continuing_ids))
        previous_totals: dict[int, Decimal] = {cid: Decimal(0) for cid in continuing_ids}

        rounds: list[dict[str, object]] = []

        retention_uuid: dict[int, str] = {c.id: c.tiebreak_uuid for c in parsed_candidates}

        def is_count_complete(*, elected_total: int, eligible_candidates: list[int]) -> bool:
            remaining_seats = seats - elected_total
            if remaining_seats <= 0:
                return True

            # Count is complete if no candidates remain eligible (the remaining seats are vacant),
            # or if the remaining eligible candidates exactly fill the remaining seats.
            # If there are fewer eligible candidates than remaining seats, the count is not
            # complete: those candidates may still be elected in subsequent iterations.
            return not eligible_candidates or len(eligible_candidates) == remaining_seats

        def eligible_candidates_list() -> list[int]:
            elected_set = set(elected)
            eligible = [cid for cid in continuing_ids if cid not in elected_set]
            eligible.sort(key=lambda cid: (candidate_name_by_id.get(cid, "").casefold(), cid))
            return eligible

        def elect_remaining_if_exact_fit(
            *,
            eligible_candidates: list[int],
            incoming_totals: Mapping[int, Decimal],
            elected_this_iteration: list[int],
            tie_breaks: list[dict[str, object]],
        ) -> tuple[list[int], list[int], list[dict[str, object]], list[int]]:
            """Elect remaining candidates when they exactly fill remaining seats.

            This is a terminal condition driven by electoral logic (not numerical
            convergence): if the remaining eligible candidates exactly fill the
            remaining seats, they are all elected immediately.
            """

            remaining_seats = seats - len(elected)
            if remaining_seats <= 0:
                return eligible_candidates, elected_this_iteration, tie_breaks, []
            if len(eligible_candidates) != remaining_seats:
                return eligible_candidates, elected_this_iteration, tie_breaks, []

            # Deterministic ordering (outcome is unaffected, but audit output should be stable).
            remaining_candidates = eligible_candidates[:]
            remaining_candidates.sort(
                key=lambda cid: (
                    previous_totals.get(cid, Decimal(0)),
                    incoming_totals.get(cid, Decimal(0)),
                    first_pref.get(cid, Decimal(0)),
                    retention_uuid.get(cid, ""),
                ),
                reverse=True,
            )

            elected.extend(remaining_candidates)
            elected_this_iteration = list(elected_this_iteration) + remaining_candidates

            # Remaining candidates are now elected, so none remain eligible.
            return [], elected_this_iteration, tie_breaks, list(remaining_candidates)

        def _tie_break_rules_trace(
            *,
            candidate_ids: list[int],
            prefer_highest: bool,
            prior_round_retained: Mapping[int, Decimal],
            cumulative_support: Mapping[int, Decimal],
            first_preferences: Mapping[int, Decimal],
        ) -> tuple[list[int], list[dict[str, object]]]:
            ordered = sorted(candidate_ids)
            trace: list[dict[str, object]] = []

            def _trace_step(*, rule: int, title: str, values: dict[int, object], remaining: list[int]) -> None:
                trace.append(
                    {
                        "rule": rule,
                        "title": title,
                        "values": {str(cid): str(values[cid]) for cid in ordered},
                        "remaining": remaining,
                    }
                )

            remaining = ordered

            values1: dict[int, Decimal] = {cid: prior_round_retained.get(cid, Decimal(0)) for cid in remaining}
            pick1 = max(values1.values()) if prefer_highest else min(values1.values())
            remaining = [cid for cid in remaining if values1[cid] == pick1]
            _trace_step(
                rule=1,
                title="prior round totals",
                values={cid: values1.get(cid, Decimal(0)) for cid in ordered},
                remaining=remaining,
            )
            if len(remaining) == 1:
                trace[-1]["result"] = "resolved"
                winner = remaining[0]
                return [winner, *[cid for cid in ordered if cid != winner]], trace
            trace[-1]["result"] = "tied"

            values2: dict[int, Decimal] = {cid: cumulative_support.get(cid, Decimal(0)) for cid in remaining}
            pick2 = max(values2.values()) if prefer_highest else min(values2.values())
            remaining = [cid for cid in remaining if values2[cid] == pick2]
            _trace_step(
                rule=2,
                title="current support totals",
                values={cid: cumulative_support.get(cid, Decimal(0)) for cid in ordered},
                remaining=remaining,
            )
            if len(remaining) == 1:
                trace[-1]["result"] = "resolved"
                winner = remaining[0]
                return [winner, *[cid for cid in ordered if cid != winner]], trace
            trace[-1]["result"] = "tied"

            values3: dict[int, Decimal] = {cid: first_preferences.get(cid, Decimal(0)) for cid in remaining}
            pick3 = max(values3.values()) if prefer_highest else min(values3.values())
            remaining = [cid for cid in remaining if values3[cid] == pick3]
            _trace_step(
                rule=3,
                title="first-preference votes",
                values={cid: first_preferences.get(cid, Decimal(0)) for cid in ordered},
                remaining=remaining,
            )
            if len(remaining) == 1:
                trace[-1]["result"] = "resolved"
                winner = remaining[0]
                return [winner, *[cid for cid in ordered if cid != winner]], trace
            trace[-1]["result"] = "tied"

            # Rule 4: deterministic lexical fallback; highest UUID wins.
            values4: dict[int, str] = {cid: retention_uuid.get(cid, "") for cid in remaining}
            pick4 = max(values4.values()) if prefer_highest else min(values4.values())
            remaining = [cid for cid in remaining if values4[cid] == pick4]
            _trace_step(
                rule=4,
                title="fixed candidate ordering identifier",
                values={cid: retention_uuid.get(cid, "") for cid in ordered},
                remaining=remaining,
            )
            trace[-1]["result"] = "resolved" if len(remaining) == 1 else "unresolved"

            # Ensure deterministic output even if UUIDs were missing or duplicated.
            ordered.sort(
                key=lambda cid: (
                    prior_round_retained.get(cid, Decimal(0)),
                    cumulative_support.get(cid, Decimal(0)),
                    first_preferences.get(cid, Decimal(0)),
                    retention_uuid.get(cid, ""),
                ),
                reverse=prefer_highest,
            )
            return ordered, trace

        def apply_exclusions(*, triggered_by: int) -> list[dict[str, object]]:
            forced_events: list[dict[str, object]] = []
            nonlocal continuing_ids

            elected_set = set(elected)
            for group in groups:
                if group.max_elected <= 0:
                    continue

                elected_in_group = len(elected_set & group.candidate_ids)
                if elected_in_group < group.max_elected:
                    continue

                for cid in sorted(group.candidate_ids):
                    if cid in elected_set:
                        continue
                    if cid not in continuing_ids:
                        continue

                    continuing_ids.remove(cid)
                    retention[cid] = Decimal(0)
                    forced_excluded.append(cid)
                    forced_events.append(
                        {
                            "candidate_id": cid,
                            "group_public_id": group.public_id,
                            "group_name": group.name,
                            "triggered_by": triggered_by,
                            "reason": "exclusion_group_max_reached",
                        }
                    )

            return forced_events

        while len(elected) < seats and continuing_ids:
            # Fixed-point iteration for current continuing set.
            for iter_idx in range(1, max_iterations + 1):
                incoming_totals, retained_totals = _distribute_votes(
                    ballots=ballots,
                    retention=retention,
                    continuing_ids=frozenset(continuing_ids),
                )

                newly_elected = [
                    cid
                    for cid in continuing_ids
                    if cid not in elected and retained_totals.get(cid, Decimal(0)) >= quota - epsilon
                ]

                quota_reached = list(newly_elected)

                # Primary ordering is by current retained total (highest first). Tie-break rules only
                # apply within groups tied on that retained total.
                newly_elected.sort(key=lambda cid: retained_totals.get(cid, Decimal(0)), reverse=True)

                tie_breaks: list[dict[str, object]] = []
                if len(newly_elected) > 1:
                    reordered: list[int] = []
                    idx = 0
                    while idx < len(newly_elected):
                        cid = newly_elected[idx]
                        retained_key = retained_totals.get(cid, Decimal(0))
                        group: list[int] = [cid]
                        idx += 1
                        while idx < len(newly_elected) and retained_totals.get(newly_elected[idx], Decimal(0)) == retained_key:
                            group.append(newly_elected[idx])
                            idx += 1

                        if len(group) == 1:
                            reordered.extend(group)
                            continue

                        ordered_group, rule_trace = _tie_break_rules_trace(
                            candidate_ids=group,
                            prefer_highest=True,
                            prior_round_retained=previous_totals,
                            cumulative_support=incoming_totals,
                            first_preferences=first_pref,
                        )
                        reordered.extend(ordered_group)
                        tie_breaks.append(
                            {
                                "type": "election_order",
                                "candidate_ids": sorted(group),
                                "ordered": ordered_group,
                                "rule_trace": rule_trace,
                            }
                        )

                    newly_elected = reordered

                remaining_seats = seats - len(elected)
                if remaining_seats <= 0:
                    newly_elected = []
                elif len(newly_elected) > remaining_seats:
                    # In edge cases (especially with fractional quotas), more candidates can meet the
                    # quota than there are remaining seats. Elect deterministically in the computed
                    # order, but never elect more than the remaining number of seats.
                    newly_elected = newly_elected[:remaining_seats]

                forced_events: list[dict[str, object]] = []
                elected_this_iteration: list[int] = []
                if newly_elected:
                    for cid in newly_elected:
                        if cid not in continuing_ids:
                            continue
                        if cid in elected:
                            continue

                        elected.append(cid)
                        elected_this_iteration.append(cid)
                        forced_events.extend(apply_exclusions(triggered_by=cid))

                # Update retention factors for all elected candidates.
                max_delta = Decimal(0)
                for cid in elected:
                    if cid not in continuing_ids:
                        continue
                    incoming = incoming_totals.get(cid, Decimal(0))
                    if incoming <= 0:
                        continue
                    new_r = quota / incoming
                    if new_r > 1:
                        new_r = Decimal(1)
                    if new_r < 0:
                        new_r = Decimal(0)
                    delta = abs(new_r - retention[cid])
                    if delta > max_delta:
                        max_delta = delta
                    retention[cid] = new_r

                numerically_converged = max_delta < epsilon and not newly_elected and not forced_events

                eligible_candidates = eligible_candidates_list()
                eligible_candidates, elected_this_iteration, tie_breaks, elected_to_fill_remaining_seats = elect_remaining_if_exact_fit(
                    eligible_candidates=eligible_candidates,
                    incoming_totals=incoming_totals,
                    elected_this_iteration=elected_this_iteration,
                    tie_breaks=tie_breaks,
                )

                elected_total = len(elected)
                count_complete = is_count_complete(elected_total=elected_total, eligible_candidates=eligible_candidates)
                round_data: dict[str, object] = {
                        "iteration": len(rounds) + 1,
                        "quota_reached": list(quota_reached),
                        "elected": list(elected_this_iteration),
                        "elected_to_fill_remaining_seats": list(elected_to_fill_remaining_seats),
                        "eliminated": None,
                        "forced_exclusions": forced_events,
                        "tie_breaks": tie_breaks,
                        "eligible_candidates": eligible_candidates,
                        "retention_factors": {str(cid): str(retention.get(cid, Decimal(0))) for cid in sorted(all_candidate_ids)},
                        "retained_totals": {
                            str(cid): str(retained_totals.get(cid, Decimal(0))) for cid in sorted(all_candidate_ids)
                        },
                        "numerically_converged": numerically_converged,
                        "max_retention_delta": str(max_delta),
                        "seats": seats,
                        "elected_total": elected_total,
                        "count_complete": count_complete,
                    }
                round_data.update(
                    generate_meek_round_explanations(
                        round_data,
                        quota=quota,
                        candidate_name_by_id=candidate_name_by_id,
                    )
                )
                rounds.append(round_data)

                previous_totals = {cid: retained_totals.get(cid, Decimal(0)) for cid in continuing_ids}

                if count_complete and (len(elected) >= seats or not eligible_candidates):
                    break
                if numerically_converged:
                    break
            else:
                raise ValueError("Meek STV did not converge within max_iterations")

            if len(elected) >= seats:
                break

            # If no candidates remain eligible, the count is complete even if seats remain vacant.
            remaining_candidates = [cid for cid in continuing_ids if cid not in elected]
            if not remaining_candidates:
                break

            # If the remaining continuing candidates exactly fill the remaining seats,
            # elect them all deterministically and finish.
            remaining_seats = seats - len(elected)
            if len(remaining_candidates) == remaining_seats:
                # Compute current vote distribution for tie-break rule 2 (cumulative support).
                incoming_totals, _retained_totals = _distribute_votes(
                    ballots=ballots,
                    retention=retention,
                    continuing_ids=frozenset(continuing_ids),
                )

                remaining_candidates.sort(
                    key=lambda cid: (
                        previous_totals.get(cid, Decimal(0)),
                        incoming_totals.get(cid, Decimal(0)),
                        first_pref.get(cid, Decimal(0)),
                        retention_uuid.get(cid, ""),
                    ),
                    reverse=True,
                )

                tie_breaks: list[dict[str, object]] = []
                if len(remaining_candidates) > 1:
                    reordered: list[int] = []
                    idx = 0
                    while idx < len(remaining_candidates):
                        cid = remaining_candidates[idx]
                        key = previous_totals.get(cid, Decimal(0))
                        group: list[int] = [cid]
                        idx += 1
                        while idx < len(remaining_candidates) and previous_totals.get(remaining_candidates[idx], Decimal(0)) == key:
                            group.append(remaining_candidates[idx])
                            idx += 1

                        if len(group) == 1:
                            reordered.extend(group)
                            continue

                        ordered_group, rule_trace = _tie_break_rules_trace(
                            candidate_ids=group,
                            prefer_highest=True,
                            prior_round_retained=previous_totals,
                            cumulative_support=incoming_totals,
                            first_preferences=first_pref,
                        )
                        reordered.extend(ordered_group)
                        tie_breaks.append(
                            {
                                "type": "election_order",
                                "candidate_ids": sorted(group),
                                "ordered": ordered_group,
                                "rule_trace": rule_trace,
                            }
                        )

                    remaining_candidates = reordered

                elected.extend(remaining_candidates)
                eligible_candidates = eligible_candidates_list()
                elected_total = len(elected)
                count_complete = is_count_complete(elected_total=elected_total, eligible_candidates=eligible_candidates)
                round_data: dict[str, object] = {
                        "iteration": len(rounds) + 1,
                        "elected": list(remaining_candidates),
                        "elected_to_fill_remaining_seats": list(remaining_candidates),
                        "eliminated": None,
                        "forced_exclusions": [],
                        "tie_breaks": tie_breaks,
                        "eligible_candidates": eligible_candidates,
                        "retention_factors": {str(cid): str(retention.get(cid, Decimal(0))) for cid in sorted(all_candidate_ids)},
                        "retained_totals": {str(cid): str(previous_totals.get(cid, Decimal(0))) for cid in sorted(all_candidate_ids)},
                        "numerically_converged": True,
                        "max_retention_delta": "0",
                        "seats": seats,
                        "elected_total": elected_total,
                        "count_complete": count_complete,
                    }
                round_data.update(
                    generate_meek_round_explanations(
                        round_data,
                        quota=quota,
                        candidate_name_by_id=candidate_name_by_id,
                    )
                )
                rounds.append(round_data)
                break

            # After convergence, if we still have seats to fill, eliminate the lowest continuing non-elected candidate.
            remaining_candidates = [cid for cid in continuing_ids if cid not in elected]
            if not remaining_candidates:
                break

            totals = _distribute_votes(
                ballots=ballots,
                retention=retention,
                continuing_ids=frozenset(continuing_ids),
            )

            incoming_totals, retained_totals = totals

            min_retained = min(retained_totals.get(cid, Decimal(0)) for cid in remaining_candidates)
            tied_for_elimination = [
                cid for cid in remaining_candidates if retained_totals.get(cid, Decimal(0)) == min_retained
            ]

            tie_breaks: list[dict[str, object]] = []
            if len(tied_for_elimination) > 1:
                ordered_group, rule_trace = _tie_break_rules_trace(
                    candidate_ids=tied_for_elimination,
                    prefer_highest=False,
                    prior_round_retained=previous_totals,
                    cumulative_support=incoming_totals,
                    first_preferences=first_pref,
                )
                to_eliminate = ordered_group[0]
                tie_breaks.append(
                    {
                        "type": "elimination",
                        "candidate_ids": sorted(tied_for_elimination),
                        "selected": to_eliminate,
                        "rule_trace": rule_trace,
                    }
                )
            else:
                to_eliminate = tied_for_elimination[0]
            continuing_ids.remove(to_eliminate)
            retention[to_eliminate] = Decimal(0)
            eliminated.append(to_eliminate)

            eligible_candidates = eligible_candidates_list()
            eligible_candidates, elected_this_iteration, tie_breaks, elected_to_fill_remaining_seats = elect_remaining_if_exact_fit(
                eligible_candidates=eligible_candidates,
                incoming_totals=incoming_totals,
                elected_this_iteration=[],
                tie_breaks=tie_breaks,
            )
            elected_total = len(elected)
            count_complete = is_count_complete(elected_total=elected_total, eligible_candidates=eligible_candidates)

            round_data: dict[str, object] = {
                    "iteration": len(rounds) + 1,
                    "elected": list(elected_this_iteration),
                    "elected_to_fill_remaining_seats": list(elected_to_fill_remaining_seats),
                    "eliminated": to_eliminate,
                    "forced_exclusions": [],
                    "tie_breaks": tie_breaks,
                    "eligible_candidates": eligible_candidates,
                    "retention_factors": {str(cid): str(retention.get(cid, Decimal(0))) for cid in sorted(all_candidate_ids)},
                    "retained_totals": {
                        str(cid): str(retained_totals.get(cid, Decimal(0))) for cid in sorted(all_candidate_ids)
                    },
                    "numerically_converged": True,
                    "max_retention_delta": "0",
                    "seats": seats,
                    "elected_total": elected_total,
                    "count_complete": count_complete,
                }
            round_data.update(
                generate_meek_round_explanations(
                    round_data,
                    quota=quota,
                    candidate_name_by_id=candidate_name_by_id,
                )
            )
            rounds.append(round_data)

        return {
            "quota": quota,
            "elected": elected[:seats],
            "eliminated": eliminated,
            "forced_excluded": forced_excluded,
            "rounds": rounds,
        }
