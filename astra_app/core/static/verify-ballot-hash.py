#!/usr/bin/env python3
"""
Verify your ballot hash (local check)

Copy the values from your vote receipt email into the variables below. This script
re-computes the ballot hash and compares it to the hash shown on your receipt.
A match means the receipt details you entered produce the same hash the system recorded.

For inclusion in the public ballot chain, run verify-ballot-chain.py.
This script runs locally and does not contact the election server.

Algorithm: SHA-256 hash of JSON payload (same as astra_app/core/models.py Ballot.compute_hash)

Source-of-truth (stable permalink):
- Ballot.compute_hash: https://github.com/AlmaLinux/astra/blob/8806e7916ec58df46a7d9f333a2e50baac31bdb7/astra_app/core/models.py
"""

# ===== YOUR BALLOT DETAILS =====
# Copy/paste these values from your vote receipt and the "Verify ballot receipt" page.

election_id = 1
voting_credential = "your-credential-id-here"

# Candidate IDs are what the system hashes, but voters usually think in usernames.
# The verify page provides a complete username -> ID mapping for the election.
candidate_ids_by_username = {
    "alice": 1,
    "bob": 2,
    "carol": 3,
}

# Your vote choices are secret. You must fill your own ranking locally
# as a comma-separated list of candidate usernames in your preferred order.
# Example: if you ranked alice then bob, use:
# ranking = "alice,bob"
ranking = "alice"

weight = 1  # The weight value from your vote receipt email
submission_nonce = "your-nonce-from-receipt-email"
expected_ballot_hash = "your-ballot-hash-from-receipt-email"

# ===== END OF USER INPUT =====


import hashlib
import json


def compute_ballot_hash(
    *,
    election_id: int,
    credential_public_id: str,
    ranking: list[int],
    weight: int,
    nonce: str,
) -> str:
    payload: dict[str, object] = {
        "election_id": election_id,
        "credential_public_id": credential_public_id,
        "ranking": ranking,
        "weight": weight,
        "nonce": nonce,
    }

    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()

ranking = [candidate_ids_by_username[x.strip()] for x in ranking.split(',')]

if __name__ == "__main__":
    if not isinstance(election_id, int) or election_id <= 0:
        raise SystemExit("election_id must be a positive integer")
    if not str(voting_credential or "").strip():
        raise SystemExit("voting_credential must be set")
    if not isinstance(ranking, list) or not all(isinstance(cid, int) for cid in ranking):
        raise SystemExit("ranking must be a list of candidate IDs (integers)")
    if not isinstance(weight, int) or weight <= 0:
        raise SystemExit("weight must be a positive integer")
    if not str(submission_nonce or "").strip():
        raise SystemExit("submission_nonce must be set")

    expected = str(expected_ballot_hash or "")
    if len(expected) != 64:
        raise SystemExit("expected_ballot_hash must be 64 characters")
    try:
        int(expected, 16)
    except ValueError:
        raise SystemExit("expected_ballot_hash must be hex")
    if expected != expected.lower():
        raise SystemExit("expected_ballot_hash must be lowercase")

    computed_hash = compute_ballot_hash(
        election_id=election_id,
        credential_public_id=voting_credential,
        ranking=ranking,
        weight=weight,
        nonce=submission_nonce,
    )

    print("Ballot Hash Verification")
    print("=" * 60)
    print(f"Election ID:     {election_id}")
    print(f"Your ranking:    {ranking}")
    print(f"Weight:          {weight}")
    print(f"Nonce:           {submission_nonce}")
    print()
    print(f"Computed hash:   {computed_hash}")
    print(f"Expected hash:   {expected_ballot_hash}")
    print()

    if computed_hash == expected_ballot_hash:
        print("✓ MATCH: Hash verified. This confirms the ballot receipt is correct.")
        raise SystemExit(0)

    print("✗ MISMATCH: Hash does not match. Double-check the values you entered above.")
    raise SystemExit(2)
