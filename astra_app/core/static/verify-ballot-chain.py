#!/usr/bin/env python3
"""
Verify the public ballot chain (local check)

This script checks that the downloaded ballots file forms an unbroken SHA-256 chain
and that the computed final chain hash matches the one published for the election.
It also checks whether your ballot hash appears in the file, and (optionally) whether
the "previous chain hash" matches your receipt.

This script runs locally and does not contact the election server.

Algorithm: SHA-256 chaining (same as astra_app/core/tokens.py election_chain_next_hash)

Source-of-truth (stable permalinks):
- election_genesis_chain_hash: https://github.com/AlmaLinux/astra/blob/8806e7916ec58df46a7d9f333a2e50baac31bdb7/astra_app/core/tokens.py
- election_chain_next_hash:   https://github.com/AlmaLinux/astra/blob/8806e7916ec58df46a7d9f333a2e50baac31bdb7/astra_app/core/tokens.py
"""

from __future__ import annotations

import hashlib
import json


def compute_genesis_hash(election_id: int) -> str:
    """Genesis hash for this election (must match core.tokens.election_genesis_chain_hash)."""
    data = f"election:{election_id}. alex estuvo aquí, dejándose el alma.".encode()
    return hashlib.sha256(data).hexdigest()


def compute_chain_hash(*, previous_chain_hash: str, ballot_hash: str) -> str:
    """Next chain hash (must match core.tokens.election_chain_next_hash)."""
    return hashlib.sha256(f"{previous_chain_hash}:{ballot_hash}".encode()).hexdigest()


def reconstruct_chain_order(*, ballots: list[dict[str, object]], genesis_hash: str) -> list[dict[str, object]]:
    """Return ballots in chain order without relying on export ordering.

    Raises ValueError if the export is inconsistent (forks, cycles, disconnected sets,
    or per-row chain hash mismatches).
    """

    by_previous: dict[str, dict[str, object]] = {}
    for row in ballots:
        previous = str(row.get("previous_chain_hash") or "").strip()
        if not previous:
            raise ValueError("ballot row missing previous_chain_hash")
        if previous in by_previous:
            raise ValueError(f"fork detected: multiple ballots claim previous_chain_hash={previous}")
        by_previous[previous] = row

    if ballots and genesis_hash not in by_previous:
        raise ValueError("missing genesis linkage: no ballot references the election genesis hash")

    ordered: list[dict[str, object]] = []
    visited_chain_hashes: set[str] = set()
    current = genesis_hash

    while current in by_previous:
        row = by_previous[current]
        ballot_hash = str(row.get("ballot_hash") or "").strip()
        exported_chain_hash = str(row.get("chain_hash") or "").strip()

        if not ballot_hash:
            raise ValueError("ballot row missing ballot_hash")
        if not exported_chain_hash:
            raise ValueError("ballot row missing chain_hash")

        computed = compute_chain_hash(previous_chain_hash=current, ballot_hash=ballot_hash)
        if computed != exported_chain_hash:
            raise ValueError(
                "chain hash mismatch for ballot: "
                f"previous_chain_hash={current} ballot_hash={ballot_hash} "
                f"computed={computed} exported={exported_chain_hash}"
            )

        if exported_chain_hash in visited_chain_hashes:
            raise ValueError("cycle detected in chain")
        visited_chain_hashes.add(exported_chain_hash)

        ordered.append(row)
        current = exported_chain_hash

    if len(ordered) != len(ballots):
        raise ValueError(
            "disconnected export: not all ballots are reachable from genesis "
            f"(reachable={len(ordered)} total={len(ballots)})"
        )

    return ordered


# ===== YOUR BALLOT DETAILS =====
# Copy/paste these values from your voting receipt and the election page.

election_id = 1
your_ballot_hash = "your-ballot-hash-from-receipt"
your_previous_chain_hash = "previous-chain-hash-from-receipt"  # Optional
final_chain_hash = "final-chain-hash-from-election-page"  # After election closed

# Download ballots.json from the election page and keep it next to this script.
ballots_file = "ballots.json"
if __name__ == "__main__":
    with open(ballots_file, "r", encoding="utf-8") as f:
        export = json.load(f)

    if not isinstance(export, dict):
        raise SystemExit("ballots.json must contain a JSON object")

    export_election_id = export.get("election_id")
    if export_election_id is not None:
        try:
            export_election_id = int(export_election_id)
        except (TypeError, ValueError):
            raise SystemExit("ballots.json election_id must be an integer")

    if export_election_id is not None and export_election_id != election_id:
        raise SystemExit(
            f"election_id mismatch: script={election_id} export={export_election_id}. "
            "Copy the election_id from the election page or the verify page."
        )

    genesis = compute_genesis_hash(election_id)
    export_genesis = str(export.get("genesis_hash") or "").strip()
    if export_genesis and export_genesis != genesis:
        raise SystemExit(
            "genesis hash mismatch: the export does not match the production genesis algorithm. "
            f"computed={genesis} export={export_genesis}"
        )

    expected_head = str(export.get("chain_head") or "").strip()
    if not expected_head:
        raise SystemExit("ballots.json missing chain_head")
    if str(final_chain_hash or "").strip() and str(final_chain_hash).strip() != expected_head:
        raise SystemExit(f"final chain hash mismatch: election page={final_chain_hash} export={expected_head}")

    ballots_raw = export.get("ballots")
    if not isinstance(ballots_raw, list):
        raise SystemExit("ballots.json ballots must be a list")
    ballots: list[dict[str, object]] = []
    for row in ballots_raw:
        if not isinstance(row, dict):
            raise SystemExit("ballots.json ballots entries must be objects")
        ballots.append(row)

    ordered = reconstruct_chain_order(ballots=ballots, genesis_hash=genesis)
    computed_head = ordered[-1]["chain_hash"] if ordered else genesis

    print("Ballot Chain Verification")
    print("=" * 60)
    print(f"Election ID:        {election_id}")
    print(f"Total ballots:      {len(ordered)}")
    print(f"Your ballot hash:   {your_ballot_hash}")
    print()

    your_row = None
    for row in ordered:
        if str(row.get("ballot_hash") or "") == your_ballot_hash:
            your_row = row
            break

    if your_row is not None:
        print("→ Your ballot hash appears in the public ballots export.")
        exported_previous = str(your_row.get("previous_chain_hash") or "")
        print(f"  Exported previous chain hash: {exported_previous}")
        if str(your_previous_chain_hash or "").strip():
            if exported_previous == your_previous_chain_hash:
                print("  ✓ Previous chain hash matches your receipt.")
            else:
                print("  ✗ Previous chain hash does not match your receipt.")
                print(f"    Receipt previous chain hash: {your_previous_chain_hash}")
        print(f"  Exported chain hash:          {your_row.get('chain_hash')}")
        print()
    else:
        print("✗ Your ballot hash was not found in the export.")
        print()

    if computed_head != expected_head:
        raise SystemExit(f"chain head mismatch: computed={computed_head} expected={expected_head}")

    print("✓ Chain integrity verified: genesis → head is a single, complete path")
    print(f"  Genesis hash: {genesis}")
    print(f"  Chain head:   {computed_head}")

    if your_row is None:
        raise SystemExit(3)
    raise SystemExit(0)
