from __future__ import annotations

import importlib.util
import random
from pathlib import Path

from django.test import SimpleTestCase

from core.models import Ballot
from core.tokens import election_chain_next_hash, election_genesis_chain_hash


def _load_script_module(*, name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VerificationScriptsTests(SimpleTestCase):
    def test_verify_ballot_hash_script_matches_ballot_compute_hash(self) -> None:
        module = _load_script_module(
            name="verify_ballot_hash",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-hash.py",
        )

        computed = module.compute_ballot_hash(
            election_id=123,
            credential_public_id="cred-public-1",
            ranking=[10, 12, 99],
            weight=2,
            nonce="n" * 32,
        )

        expected = Ballot.compute_hash(
            election_id=123,
            credential_public_id="cred-public-1",
            ranking=[10, 12, 99],
            weight=2,
            nonce="n" * 32,
        )
        self.assertEqual(computed, expected)

    def test_verify_ballot_chain_script_reconstructs_chain_from_unordered_export(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        election_id = 77
        genesis = election_genesis_chain_hash(election_id)

        ballot_hashes = [
            Ballot.compute_hash(
                election_id=election_id,
                credential_public_id=f"cred-{i}",
                ranking=[i],
                weight=1,
                nonce="0" * 32,
            )
            for i in range(1, 4)
        ]

        ballots: list[dict[str, str]] = []
        prev = genesis
        for ballot_hash in ballot_hashes:
            chain_hash = election_chain_next_hash(previous_chain_hash=prev, ballot_hash=ballot_hash)
            ballots.append(
                {
                    "ballot_hash": ballot_hash,
                    "previous_chain_hash": prev,
                    "chain_hash": chain_hash,
                }
            )
            prev = chain_hash

        export = {
            "election_id": election_id,
            "genesis_hash": genesis,
            "chain_head": prev,
            "ballots": ballots,
        }

        shuffled = list(ballots)
        random.shuffle(shuffled)

        ordered = module.reconstruct_chain_order(ballots=shuffled, genesis_hash=genesis)
        self.assertEqual([row["ballot_hash"] for row in ordered], ballot_hashes)

        # Sanity: order-independent verification should still reach the same head.
        computed_head = ordered[-1]["chain_hash"] if ordered else genesis
        self.assertEqual(computed_head, export["chain_head"])
