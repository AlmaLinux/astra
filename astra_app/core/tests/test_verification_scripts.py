
import base64
import hashlib
import importlib.util
import io
import json
import random
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

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

    def test_verify_ballot_chain_script_rekor_canonical_digest_check_passes(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        event_payload = {"chain_head": "abc"}
        canonical_bytes = module._canonical_bytes(
            event_type="election_closed",
            payload=event_payload,
        )
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        audit_data = {
            "audit_log": [
                {
                    "event_type": "election_closed",
                    "payload": event_payload,
                    "timestamping": {
                        "canonical_message_version": 1,
                        "message_digest_hex": digest,
                        "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                    },
                }
            ]
        }

        output = io.StringIO()
        with redirect_stdout(output):
            module.verify_rekor_attestations(audit_data=audit_data, verify_online=False)

        self.assertIn("digest", output.getvalue().lower())

    def test_verify_ballot_chain_script_rekor_network_error_warns_without_exit(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        event_payload = {"chain_head": "abc"}
        canonical_bytes = module._canonical_bytes(
            event_type="election_closed",
            payload=event_payload,
        )
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        audit_data = {
            "audit_log": [
                {
                    "event_type": "election_closed",
                    "payload": event_payload,
                    "timestamping": {
                        "canonical_message_version": 1,
                        "message_digest_hex": digest,
                        "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                    },
                }
            ]
        }

        output = io.StringIO()
        with (
            redirect_stdout(output),
            patch("urllib.request.urlopen", side_effect=URLError("network down")),
        ):
            module.verify_rekor_attestations(audit_data=audit_data, verify_online=True)

        self.assertIn("warning", output.getvalue().lower())

    def test_verify_ballot_chain_script_online_warns_for_multi_key_wrapper(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        event_payload = {"chain_head": "abc"}
        canonical_bytes = module._canonical_bytes(
            event_type="election_closed",
            payload=event_payload,
        )
        digest = hashlib.sha256(canonical_bytes).hexdigest()

        rekor_body = {
            "spec": {
                "data": {
                    "hash": {
                        "value": digest,
                    }
                }
            }
        }
        body_b64 = base64.b64encode(json.dumps(rekor_body).encode("utf-8")).decode("ascii")

        wrapper_response = {
            "uuid-1": {"body": body_b64},
            "uuid-2": {"body": body_b64},
        }

        class _FakeResponse:
            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                del exc_type
                del exc
                del tb
                return False

            def read(self) -> bytes:
                return json.dumps(wrapper_response).encode("utf-8")

        audit_data = {
            "audit_log": [
                {
                    "event_type": "election_closed",
                    "payload": event_payload,
                    "timestamping": {
                        "canonical_message_version": 1,
                        "message_digest_hex": digest,
                        "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                    },
                }
            ]
        }

        output = io.StringIO()
        with (
            redirect_stdout(output),
            patch("urllib.request.urlopen", return_value=_FakeResponse()),
        ):
            module.verify_rekor_attestations(audit_data=audit_data, verify_online=True)

        text = output.getvalue().lower()
        self.assertIn("warning", text)
        self.assertNotIn("online: pass", text)
