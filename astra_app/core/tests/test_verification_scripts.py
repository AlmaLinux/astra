
import base64
import datetime
import hashlib
import importlib.util
import io
import json
import random
import textwrap
from collections.abc import Mapping
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch
from urllib.error import URLError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import SimpleTestCase

from core.models import Ballot
from core.tokens import election_chain_anchor_hash, election_chain_next_hash, election_genesis_chain_hash


def _load_script_module(*, name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VerificationScriptsTests(SimpleTestCase):
    TRUSTED_REKOR_PUBLIC_KEY_PEM = textwrap.dedent(
        """\
        -----BEGIN PUBLIC KEY-----
        MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE6VF2PVPmOMIg5eRV1+MIK/hRXy53
        4wHhKn77HEEZP5mfbkOtEkVEVsO8W4X0dsubDOlAcx49ckaAH/KGMsHPkQ==
        -----END PUBLIC KEY-----
        """
    ).strip()
    TRUSTED_REKOR_PUBLIC_KEY_SHA256 = "99f3b7b90a6d81ac36e8aaf8066d3da0d7ccd49dab06995ad6eeb83384a0dd12"

    def _build_valid_v1_manifest(self, *, election_id: int = 7, name: str = "Election 7") -> dict[str, object]:
        return {
            "version": 1,
            "election": {
                "id": election_id,
                "name": name,
                "start_datetime": "2026-01-02T03:04:05Z",
                "number_of_seats": 1,
                "quorum": 10,
                "eligible_group_cn": "voters",
            },
            "tally_rule": {
                "algorithm": "Meek STV (High-Precision Variant)",
                "algorithm_version": "1.0",
                "spec_identity": "docs/runbooks/meek-stv-elections.md",
                "epsilon": "1E-28",
                "max_iterations": 200,
            },
            "candidates": [
                {
                    "id": 1,
                    "freeipa_username": "alice",
                    "nominated_by": "nominator",
                    "tiebreak_uuid": "00000000-0000-0000-0000-000000000001",
                }
            ],
            "exclusion_groups": [
                {
                    "public_id": "10000000-0000-0000-0000-000000000001",
                    "name": "Employees",
                    "max_elected": 1,
                    "candidate_ids": [1],
                }
            ],
        }

    def _generate_signing_material(self) -> tuple[ec.EllipticCurvePrivateKey, str, str]:
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()
        public_key_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        public_key_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_key_fingerprint = hashlib.sha256(public_key_der).hexdigest()
        return private_key, public_key_pem, public_key_fingerprint

    def _rekor_wrapper_response(
        self,
        *,
        module,
        canonical_bytes: bytes,
        expected_digest: str,
        signing_key: ec.EllipticCurvePrivateKey,
        integrated_time: int | None = None,
    ) -> dict[str, dict[str, object]]:
        signature_der = signing_key.sign(canonical_bytes, ec.ECDSA(hashes.SHA256()))
        public_key_pem = signing_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        resolved_integrated_time = integrated_time
        if resolved_integrated_time is None:
            resolved_integrated_time = int(datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC).timestamp())
        body = {
            "apiVersion": "0.0.1",
            "kind": "hashedrekord",
            "spec": {
                "data": {
                    "hash": {
                        "algorithm": "sha256",
                        "value": expected_digest,
                    }
                },
                "signature": {
                    "content": base64.b64encode(signature_der).decode("ascii"),
                    "publicKey": {
                        "content": base64.b64encode(public_key_pem).decode("ascii"),
                    },
                },
            },
        }
        return {
            "uuid-1": {
                "integratedTime": resolved_integrated_time,
                "body": base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii"),
            }
        }

    def _fake_urlopen_response(self, wrapper_response: Mapping[str, object]):
        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                del exc_type
                del exc
                del tb
                return False

            def read(self) -> bytes:
                return json.dumps(wrapper_response).encode("utf-8")

        return _FakeResponse()

    def _build_v2_bundle_with_optional_ballot(
        self,
        *,
        election_id: int = 7,
        include_ballot: bool = False,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
        manifest = self._build_valid_v1_manifest(election_id=election_id)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=election_id, config_manifest_sha256=manifest_digest)

        ballot_hash = ""
        chain_head = anchor_hash
        ballots: list[dict[str, str]] = []
        if include_ballot:
            ballot_hash = Ballot.compute_hash(
                election_id=election_id,
                credential_public_id="cred-1",
                ranking=[1],
                weight=1,
                nonce="0" * 32,
            )
            chain_head = election_chain_next_hash(previous_chain_hash=anchor_hash, ballot_hash=ballot_hash)
            ballots.append(
                {
                    "ballot_hash": ballot_hash,
                    "previous_chain_hash": anchor_hash,
                    "chain_hash": chain_head,
                }
            )

        ballots_export: dict[str, object] = {
            "election_id": election_id,
            "chain_version": 2,
            "chain_root_kind": "config_anchor_v2",
            "genesis_hash": anchor_hash,
            "chain_head": chain_head,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "ballots": ballots,
        }
        audit_export: dict[str, object] = {
            "election_id": election_id,
            "chain_version": 2,
            "chain_root_kind": "config_anchor_v2",
            "genesis_hash": anchor_hash,
            "chain_head": chain_head,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "audit_log": [
                {
                    "event_type": "election_started",
                    "payload": {
                        "chain_version": 2,
                        "config_manifest_version": 1,
                        "config_manifest_sha256": manifest_digest,
                        "chain_anchor_hash": anchor_hash,
                        "config_manifest": manifest,
                    },
                }
            ],
        }
        return ballots_export, audit_export, {
            "manifest_digest": manifest_digest,
            "anchor_hash": anchor_hash,
            "ballot_hash": ballot_hash,
            "chain_head": chain_head,
        }

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

    def test_verify_audit_log_script_ships_with_default_trusted_attestation_signer(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        self.assertEqual(module.trusted_public_key_pem.strip(), self.TRUSTED_REKOR_PUBLIC_KEY_PEM)
        self.assertEqual(module.trusted_public_key_sha256, self.TRUSTED_REKOR_PUBLIC_KEY_SHA256)

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

    def test_verify_ballot_chain_script_reports_v2_untrusted_local_only(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)

        result = module.verify_export_bundle(
            ballots_export=ballots_export,
            audit_export=audit_export,
            election_id=7,
            ballot_receipt_code="",
            previous_ledger_hash="",
            current_ledger_hash=metadata["chain_head"],
            verify_rekor_online=False,
        )

        self.assertEqual(result["status"], "untrusted_local_only")

    def test_verify_ballot_chain_script_exposes_top_level_receipt_and_rekor_config(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        self.assertTrue(hasattr(module, "receipt_previous_ledger_hash"))
        self.assertTrue(hasattr(module, "receipt_current_ledger_hash"))
        self.assertTrue(hasattr(module, "final_election_chain_head"))
        self.assertTrue(hasattr(module, "verify_rekor_online"))

    def test_verify_ballot_chain_script_rejects_missing_v2_config_manifest_version(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        del ballots_export["config_manifest_version"]

        with self.assertRaisesRegex(ValueError, "config_manifest_version"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_rejects_unknown_v2_config_manifest_version(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        ballots_export["config_manifest_version"] = 2
        audit_export["config_manifest_version"] = 2
        audit_log = cast(list[object], audit_export["audit_log"])
        election_started = cast(dict[str, object], audit_log[0])
        payload = cast(dict[str, object], election_started["payload"])
        manifest = cast(dict[str, object], payload["config_manifest"])
        payload["config_manifest_version"] = 2
        manifest["version"] = 2

        with self.assertRaisesRegex(ValueError, "config_manifest_version"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_rejects_mismatched_v2_config_manifest_version(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        audit_log = cast(list[object], audit_export["audit_log"])
        election_started = cast(dict[str, object], audit_log[0])
        payload = cast(dict[str, object], election_started["payload"])
        payload["config_manifest_version"] = 2

        with self.assertRaisesRegex(ValueError, "config_manifest_version"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_finds_v2_receipt_and_returns_hashes(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(
            election_id=7,
            include_ballot=True,
        )

        result = module.verify_public_ballot_export(
            ballots_export=ballots_export,
            audit_export=audit_export,
            election_id=7,
            ballot_receipt_code=metadata["ballot_hash"],
            previous_ledger_hash="",
            current_ledger_hash="",
            receipt_previous_ledger_hash=metadata["anchor_hash"],
            receipt_current_ledger_hash=metadata["chain_head"],
            final_election_chain_head=metadata["chain_head"],
            verify_rekor_online=True,
        )

        self.assertEqual(result["status"], "untrusted_local_only")
        self.assertTrue(result["receipt_found"])
        self.assertEqual(result["receipt_previous_chain_hash"], metadata["anchor_hash"])
        self.assertEqual(result["receipt_chain_hash"], metadata["chain_head"])
        self.assertTrue(result["rekor_online_requested"])
        self.assertIn("verify-audit-log.py", str(result["rekor_guidance"]))

    def test_verify_ballot_chain_script_rejects_v2_receipt_previous_hash_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(
            election_id=7,
            include_ballot=True,
        )

        with self.assertRaisesRegex(ValueError, "receipt previous ledger hash"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code=metadata["ballot_hash"],
                previous_ledger_hash="",
                current_ledger_hash="",
                receipt_previous_ledger_hash="0" * 64,
                final_election_chain_head=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_rejects_v2_receipt_current_hash_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(
            election_id=7,
            include_ballot=True,
        )

        with self.assertRaisesRegex(ValueError, "receipt current ledger hash"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code=metadata["ballot_hash"],
                previous_ledger_hash="",
                current_ledger_hash="",
                receipt_previous_ledger_hash=metadata["anchor_hash"],
                receipt_current_ledger_hash="0" * 64,
                final_election_chain_head=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_wraps_v2_bundle_for_cli_reporting(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)

        result = module.verify_public_ballot_export(
            ballots_export=ballots_export,
            audit_export=audit_export,
            election_id=7,
            ballot_receipt_code="",
            previous_ledger_hash="",
            current_ledger_hash=metadata["chain_head"],
        )

        self.assertEqual(result["status"], "untrusted_local_only")
        self.assertEqual(result["chain_head"], metadata["chain_head"])
        self.assertEqual(result["config_manifest_sha256"], metadata["manifest_digest"])
        self.assertEqual(result["genesis_hash"], metadata["anchor_hash"])
        self.assertNotIn("chain_anchor_hash", result)
        self.assertNotIn("chain_root_hash", result)

    def test_verify_ballot_chain_script_accepts_legacy_v2_chain_root_hash_alias(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        del ballots_export["genesis_hash"]
        del audit_export["genesis_hash"]
        ballots_export["chain_root_hash"] = metadata["anchor_hash"]
        audit_export["chain_root_hash"] = metadata["anchor_hash"]

        result = module.verify_public_ballot_export(
            ballots_export=ballots_export,
            audit_export=audit_export,
            election_id=7,
            ballot_receipt_code="",
            previous_ledger_hash="",
            current_ledger_hash=metadata["chain_head"],
        )

        self.assertEqual(result["status"], "untrusted_local_only")
        self.assertEqual(result["genesis_hash"], metadata["anchor_hash"])
        self.assertNotIn("chain_anchor_hash", result)
        self.assertNotIn("chain_root_hash", result)

    def test_verify_ballot_chain_script_accepts_legacy_v2_chain_anchor_hash_alias_without_genesis_hash(
        self,
    ) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        del ballots_export["genesis_hash"]
        del audit_export["genesis_hash"]
        ballots_export["chain_anchor_hash"] = metadata["anchor_hash"]
        audit_export["chain_anchor_hash"] = metadata["anchor_hash"]

        result = module.verify_public_ballot_export(
            ballots_export=ballots_export,
            audit_export=audit_export,
            election_id=7,
            ballot_receipt_code="",
            previous_ledger_hash="",
            current_ledger_hash=metadata["chain_head"],
        )

        self.assertEqual(result["status"], "untrusted_local_only")
        self.assertEqual(result["genesis_hash"], metadata["anchor_hash"])
        self.assertNotIn("chain_anchor_hash", result)
        self.assertNotIn("chain_root_hash", result)

    def test_verify_ballot_chain_script_rejects_mismatched_legacy_v2_chain_root_hash_alias(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        ballots_export["chain_root_hash"] = "0" * 64
        audit_export["chain_root_hash"] = "0" * 64

        with self.assertRaisesRegex(
            ValueError,
            r"public-ballots\.json chain_root_hash alias does not match genesis_hash",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_guides_v2_current_hash_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, _metadata = self._build_v2_bundle_with_optional_ballot(
            election_id=7,
            include_ballot=True,
        )

        with self.assertRaisesRegex(
            ValueError,
            "Entered current ledger hash does not match public-ballots.json chain_head",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash="0" * 64,
            )

    def test_verify_ballot_chain_script_rejects_v1_root_kind_contract_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        election_id = 77
        genesis = election_genesis_chain_hash(election_id)
        ballot_hash = Ballot.compute_hash(
            election_id=election_id,
            credential_public_id="cred-1",
            ranking=[1],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = election_chain_next_hash(previous_chain_hash=genesis, ballot_hash=ballot_hash)

        with self.assertRaisesRegex(ValueError, "chain_root_kind"):
            module.verify_public_ballot_export(
                ballots_export={
                    "election_id": election_id,
                    "chain_version": 1,
                    "chain_root_kind": "genesis_v1",
                    "genesis_hash": genesis,
                    "chain_head": chain_hash,
                    "ballots": [
                        {
                            "ballot_hash": ballot_hash,
                            "previous_chain_hash": genesis,
                            "chain_hash": chain_hash,
                        }
                    ],
                },
                audit_export=None,
                election_id=election_id,
                ballot_receipt_code=ballot_hash,
                previous_ledger_hash=genesis,
                current_ledger_hash=chain_hash,
            )

    def test_verify_ballot_chain_script_guides_v1_current_hash_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        election_id = 77
        genesis = election_genesis_chain_hash(election_id)
        ballot_hash = Ballot.compute_hash(
            election_id=election_id,
            credential_public_id="cred-1",
            ranking=[1],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = election_chain_next_hash(previous_chain_hash=genesis, ballot_hash=ballot_hash)

        with self.assertRaisesRegex(
            ValueError,
            "Entered current ledger hash does not match public-ballots.json chain_head",
        ):
            module.verify_public_ballot_export(
                ballots_export={
                    "election_id": election_id,
                    "chain_version": 1,
                    "chain_root_kind": "legacy_genesis",
                    "genesis_hash": genesis,
                    "chain_head": chain_hash,
                    "ballots": [
                        {
                            "ballot_hash": ballot_hash,
                            "previous_chain_hash": genesis,
                            "chain_hash": chain_hash,
                        }
                    ],
                },
                audit_export=None,
                election_id=election_id,
                ballot_receipt_code=ballot_hash,
                previous_ledger_hash=genesis,
                current_ledger_hash="0" * 64,
            )

    def test_verify_ballot_chain_script_accepts_v1_genesis_hash_canonical_field(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        election_id = 77
        genesis = election_genesis_chain_hash(election_id)
        ballot_hash = Ballot.compute_hash(
            election_id=election_id,
            credential_public_id="cred-1",
            ranking=[1],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = election_chain_next_hash(previous_chain_hash=genesis, ballot_hash=ballot_hash)

        result = module.verify_public_ballot_export(
            ballots_export={
                "election_id": election_id,
                "chain_version": 1,
                "chain_root_kind": "legacy_genesis",
                "genesis_hash": genesis,
                "chain_head": chain_hash,
                "ballots": [
                    {
                        "ballot_hash": ballot_hash,
                        "previous_chain_hash": genesis,
                        "chain_hash": chain_hash,
                    }
                ],
            },
            audit_export=None,
            election_id=election_id,
            ballot_receipt_code=ballot_hash,
            previous_ledger_hash=genesis,
            current_ledger_hash=chain_hash,
        )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["genesis_hash"], genesis)
        self.assertNotIn("chain_root_hash", result)

    def test_verify_ballot_chain_script_rejects_requested_v2_election_id_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        ballots_export["election_id"] = 8
        audit_export["election_id"] = 8

        with self.assertRaisesRegex(
            ValueError,
            r"election_id mismatch: exports do not match the requested election_id; use the public-ballots\.json/public-audit\.json pair for this election\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_guides_v2_chain_version_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        audit_export["chain_version"] = 1

        with self.assertRaisesRegex(
            ValueError,
            r"chain_version mismatch between public-ballots\.json and public-audit\.json; use the matching public-ballots/public-audit export pair for this election\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_requires_v2_audit_log(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        del audit_export["audit_log"]

        with self.assertRaisesRegex(
            ValueError,
            r"public-audit\.json must include audit_log for v2 verification; use the matching public-audit\.json exported with this public-ballots\.json\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_guides_v2_chain_root_kind_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        ballots_export["chain_root_kind"] = "legacy_genesis"
        audit_export["chain_root_kind"] = "legacy_genesis"

        with self.assertRaisesRegex(
            ValueError,
            r"v2 exports must use chain_root_kind=config_anchor_v2; use the matching public-ballots/public-audit export pair for this election\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_rejects_mismatched_publication_bundle_timestamp(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        ballots_export["publication_bundle"] = {"published_at": "2026-04-11T10:15:00Z"}
        audit_export["publication_bundle"] = {"published_at": "2026-04-11T10:16:00Z"}

        with self.assertRaisesRegex(
            ValueError,
            r"publication_bundle\.published_at mismatch between public-ballots\.json and public-audit\.json; use files from the same published bundle\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_ballot_chain_script_guides_chain_head_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        election_id = 77
        genesis = election_genesis_chain_hash(election_id)
        ballot_hash = Ballot.compute_hash(
            election_id=election_id,
            credential_public_id="cred-1",
            ranking=[1],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = election_chain_next_hash(previous_chain_hash=genesis, ballot_hash=ballot_hash)
        exported_chain_head = "f" * 64

        with self.assertRaisesRegex(
            ValueError,
            r"chain head mismatch: reconstructed chain does not match exported chain_head; re-download the published export\(s\) and rerun verification\.",
        ):
            module.verify_public_ballot_export(
                ballots_export={
                    "election_id": election_id,
                    "chain_version": 1,
                    "chain_root_kind": "legacy_genesis",
                    "genesis_hash": genesis,
                    "chain_head": exported_chain_head,
                    "ballots": [
                        {
                            "ballot_hash": ballot_hash,
                            "previous_chain_hash": genesis,
                            "chain_hash": chain_hash,
                        }
                    ],
                },
                audit_export=None,
                election_id=election_id,
                ballot_receipt_code=ballot_hash,
                previous_ledger_hash=genesis,
                current_ledger_hash=exported_chain_head,
            )

    def test_verify_ballot_chain_script_guides_chain_anchor_mismatch(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        mismatched_anchor = "0" * 64
        ballots_export["genesis_hash"] = mismatched_anchor
        audit_export["genesis_hash"] = mismatched_anchor

        with self.assertRaisesRegex(
            ValueError,
            r"genesis_hash mismatch: computed genesis hash does not match exported genesis_hash; use the matching public-audit\.json and config manifest for this election\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_audit_log_reports_v2_genesis_hash_mismatch_reason(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "genesis_hash": "0" * 64,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason"], "genesis_hash mismatch")

    def test_verify_ballot_chain_script_guides_missing_v2_election_started_event(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        audit_export["audit_log"] = []

        with self.assertRaisesRegex(
            ValueError,
            r"public-audit\.json missing election_started event for v2 verification; use the matching public-audit\.json exported with this public-ballots\.json\.",
        ):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_audit_log_script_runs_offline_by_default(self) -> None:
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
            patch("urllib.request.urlopen") as urlopen,
        ):
            found_any, all_pass = module.verify_rekor_attestations(
                audit_data=audit_data,
                verify_online=module.verify_rekor_online,
            )

        self.assertTrue(found_any)
        self.assertTrue(all_pass)
        urlopen.assert_not_called()
        self.assertNotIn("online:", output.getvalue().lower())

    def test_verify_audit_log_reports_v2_election_definition_as_untrusted_local_only(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "untrusted_local_only")
        self.assertEqual(result["chain_version"], 2)
        self.assertEqual(result["config_manifest_sha256"], manifest_digest)
        self.assertEqual(result["genesis_hash"], anchor_hash)
        self.assertNotIn("chain_anchor_hash", result)

    def test_verify_audit_log_accepts_v2_manifest_without_candidate_description_or_url(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "untrusted_local_only")

    def test_verify_audit_log_rejects_missing_v2_config_manifest_version(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("config_manifest_version", str(result.get("reason") or ""))

    def test_verify_audit_log_rejects_unknown_v2_config_manifest_version(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest["version"] = 2
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 2,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("config_manifest_version", str(result.get("reason") or ""))

    def test_verify_audit_log_rejects_mismatched_v2_config_manifest_version(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 2,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("config_manifest_version", str(result.get("reason") or ""))

    def test_verify_audit_log_marks_v2_digest_mismatch_invalid(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": {
                            "chain_version": 2,
                            "config_manifest_version": 1,
                            "config_manifest_sha256": manifest_digest,
                            "chain_anchor_hash": anchor_hash,
                            "config_manifest": manifest,
                        },
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": "0" * 64,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("digest", result["reason"])

    def test_verify_audit_log_online_v2_rejects_untrusted_signer(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, trusted_public_key_pem, _trusted_fingerprint = self._generate_signing_material()
        attacker_key, _attacker_public_key_pem, _attacker_fingerprint = self._generate_signing_material()
        del trusted_key

        manifest = self._build_valid_v1_manifest(election_id=7, name="Tampered election")
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=attacker_key,
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_pem=trusted_public_key_pem,
            )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("trusted", str(result.get("reason") or "").lower())

    def test_verify_audit_log_online_v2_accepts_trusted_signer(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, _trusted_public_key_pem, trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_sha256=trusted_fingerprint,
            )

        self.assertEqual(result["status"], "valid")

    def test_verify_audit_log_online_v2_uses_shipped_trusted_signer_by_default(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        def fake_verify_online_rekor_entry(**kwargs):
            self.assertEqual(kwargs["trusted_pem_value"].strip(), self.TRUSTED_REKOR_PUBLIC_KEY_PEM)
            self.assertEqual(kwargs["trusted_fingerprint_value"], self.TRUSTED_REKOR_PUBLIC_KEY_SHA256)
            integrated_time = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
            return {
                "digest_matches": True,
                "rekor_digest": digest,
                "embedded_public_key_sha256": self.TRUSTED_REKOR_PUBLIC_KEY_SHA256,
                "integrated_time": integrated_time,
                "integrated_time_utc": module._format_utc(integrated_time),
                "signature_valid": True,
                "trusted_configured": True,
                "trusted_public_key_sha256": self.TRUSTED_REKOR_PUBLIC_KEY_SHA256,
                "signer_trusted": True,
            }

        with patch.object(module, "_verify_online_rekor_entry", side_effect=fake_verify_online_rekor_entry):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                            },
                        }
                    ]
                },
                verify_online=True,
            )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["trusted_public_key_sha256"], self.TRUSTED_REKOR_PUBLIC_KEY_SHA256)

    def test_verify_audit_log_online_v2_allows_explicit_trust_override_over_shipped_default(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, trusted_public_key_pem, trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_pem=trusted_public_key_pem,
                trusted_public_key_sha256=trusted_fingerprint,
            )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["trusted_public_key_sha256"], trusted_fingerprint)

    def test_verify_audit_log_online_v2_rejects_untrusted_attestation_signer_with_updated_reason(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, _trusted_public_key_pem, _trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_sha256="0" * 64,
            )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(
            result.get("reason"),
            (
                "embedded attestation signer fingerprint does not match trusted attestation signer "
                f"(embedded={module._openssl_public_key_fingerprint_sha256(pem_bytes=trusted_key.public_key().public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo))} "
                "trusted=0000000000000000000000000000000000000000000000000000000000000000)"
            ),
        )

    def test_verify_audit_log_online_v2_accepts_rekor_timestamp_within_tolerance(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, _trusted_public_key_pem, trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
        )
        wrapper_response["uuid-1"]["integratedTime"] = int(
            datetime.datetime(2026, 1, 2, 3, 4, 8, tzinfo=datetime.UTC).timestamp()
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                                "rekor_integrated_time": "2026-01-02T03:04:08Z",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_sha256=trusted_fingerprint,
            )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["rekor_timestamp_delta_seconds"], 3)

    def test_verify_audit_log_online_v2_reads_integrated_time_from_rekor_wrapper(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, _trusted_public_key_pem, trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
            integrated_time=int(datetime.datetime(2026, 1, 2, 3, 4, 7, tzinfo=datetime.UTC).timestamp()),
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                                "rekor_integrated_time": "2026-01-02T03:04:07Z",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_sha256=trusted_fingerprint,
            )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["rekor_integrated_time"], "2026-01-02T03:04:07Z")

    def test_verify_audit_log_online_v2_rejects_rekor_timestamp_outside_tolerance(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, _trusted_public_key_pem, trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
        )
        wrapper_response["uuid-1"]["integratedTime"] = int(
            datetime.datetime(2026, 1, 2, 3, 4, 16, tzinfo=datetime.UTC).timestamp()
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "timestamp_utc": "2026-01-02T03:04:05Z",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                                "rekor_integrated_time": "2026-01-02T03:04:16Z",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_sha256=trusted_fingerprint,
            )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("timestamp", str(result.get("reason") or "").lower())

    def test_verify_audit_log_online_v2_without_precise_exported_timestamp_stays_untrusted(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        trusted_key, _trusted_public_key_pem, trusted_fingerprint = self._generate_signing_material()
        manifest = self._build_valid_v1_manifest(election_id=7)

        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        canonical_bytes = module._canonical_bytes(event_type="election_started", payload=payload)
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        wrapper_response = self._rekor_wrapper_response(
            module=module,
            canonical_bytes=canonical_bytes,
            expected_digest=digest,
            signing_key=trusted_key,
        )
        wrapper_response["uuid-1"]["integratedTime"] = int(
            datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC).timestamp()
        )

        with patch("urllib.request.urlopen", return_value=self._fake_urlopen_response(wrapper_response)):
            result = module.evaluate_v2_election_definition(
                audit_data={
                    "audit_log": [
                        {
                            "event_type": "election_started",
                            "timestamp": "2026-01-02",
                            "payload": payload,
                            "timestamping": {
                                "canonical_message_version": 1,
                                "message_digest_hex": digest,
                                "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                                "rekor_integrated_time": "2026-01-02T03:04:05Z",
                            },
                        }
                    ]
                },
                verify_online=True,
                trusted_public_key_sha256=trusted_fingerprint,
            )

        self.assertEqual(result["status"], "untrusted_local_only")
        self.assertIn("precision", str(result.get("reason") or "").lower())

    def test_verify_ballot_chain_script_rejects_incomplete_v2_manifest_schema(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        audit_log = cast(list[object], audit_export["audit_log"])
        election_started = cast(dict[str, object], audit_log[0])
        payload = cast(dict[str, object], election_started["payload"])
        manifest = cast(dict[str, object], payload["config_manifest"])
        election_section = cast(dict[str, object], manifest["election"])
        del election_section["name"]

        with self.assertRaisesRegex(ValueError, "config_manifest.*name"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

    def test_verify_audit_log_rejects_incomplete_v2_manifest_schema(self) -> None:
        module = _load_script_module(
            name="verify_audit_log",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-audit-log.py",
        )

        manifest = self._build_valid_v1_manifest(election_id=7)
        tally_rule = cast(dict[str, object], manifest["tally_rule"])
        del tally_rule["algorithm"]
        manifest_digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        anchor_hash = election_chain_anchor_hash(election_id=7, config_manifest_sha256=manifest_digest)
        payload = {
            "chain_version": 2,
            "config_manifest_version": 1,
            "config_manifest_sha256": manifest_digest,
            "chain_anchor_hash": anchor_hash,
            "config_manifest": manifest,
        }
        digest = hashlib.sha256(module._canonical_bytes(event_type="election_started", payload=payload)).hexdigest()

        result = module.evaluate_v2_election_definition(
            audit_data={
                "audit_log": [
                    {
                        "event_type": "election_started",
                        "payload": payload,
                        "timestamping": {
                            "canonical_message_version": 1,
                            "message_digest_hex": digest,
                            "rekor_entry_url": "https://rekor.example/api/v1/log/entries/uuid-1",
                        },
                    }
                ]
            },
            verify_online=False,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("config_manifest", str(result.get("reason") or ""))

    def test_verify_ballot_chain_script_rejects_corrupted_v2_payload_anchor_when_top_level_matches(self) -> None:
        module = _load_script_module(
            name="verify_ballot_chain",
            path=Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py",
        )

        ballots_export, audit_export, metadata = self._build_v2_bundle_with_optional_ballot(election_id=7)
        audit_log = cast(list[object], audit_export["audit_log"])
        election_started = cast(dict[str, object], audit_log[0])
        payload = cast(dict[str, object], election_started["payload"])
        payload["chain_anchor_hash"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "payload.*genesis_hash"):
            module.verify_public_ballot_export(
                ballots_export=ballots_export,
                audit_export=audit_export,
                election_id=7,
                ballot_receipt_code="",
                previous_ledger_hash="",
                current_ledger_hash=metadata["chain_head"],
            )

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
