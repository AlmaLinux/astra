import datetime
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from core.elections_services import build_public_audit_export
from core.elections_timestamping import (
    _attest_entry,
    get_canonical_bytes,
    get_public_payload,
    schedule_attestation,
)
from core.models import AuditLogEntry, Election


class _FakePublicKey:
    def public_bytes(self, encoding, format):
        del encoding
        del format
        return b"fake-public-key"


class _FakePrivateKey:
    def public_key(self) -> _FakePublicKey:
        return _FakePublicKey()

    def sign(self, data: bytes, signature_algorithm) -> bytes:
        del data
        del signature_algorithm
        return b"fake-signature-der"


class ElectionsTimestampingTests(TestCase):
    def _create_election(self) -> Election:
        now = timezone.now()
        return Election.objects.create(
            name="Timestamping election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

    def _create_public_entry(self, *, event_type: str, payload: object) -> AuditLogEntry:
        election = self._create_election()
        return AuditLogEntry.objects.create(
            election=election,
            event_type=event_type,
            payload=payload,
            is_public=True,
        )

    def test_get_public_payload_strips_actor_from_dict_payload(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"actor": "alice", "chain_head": "abc"},
        )

        self.assertEqual(get_public_payload(entry), {"chain_head": "abc"})

    def test_get_public_payload_wraps_non_dict_payload(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload=["a", "b", "c"],
        )

        self.assertEqual(get_public_payload(entry), {"data": ["a", "b", "c"]})

    def test_get_canonical_bytes_is_deterministic_sorted_json(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"z": 2, "actor": "alice", "a": 1},
        )

        self.assertEqual(
            get_canonical_bytes(entry),
            b'{"event_type":"election_closed","payload":{"a":1,"z":2}}',
        )

    @override_settings(
        ELECTION_REKOR_ENDPOINT="",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
    )
    def test_schedule_attestation_is_noop_when_endpoint_is_empty(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )

        with patch("core.elections_timestamping._attest_entry") as attest_mock:
            schedule_attestation(entry)

        attest_mock.assert_not_called()

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
    )
    def test_schedule_attestation_is_noop_for_non_attested_event(self) -> None:
        entry = self._create_public_entry(
            event_type="ballot_submitted",
            payload={"ballot_hash": "abc"},
        )

        with patch("core.elections_timestamping._attest_entry") as attest_mock:
            schedule_attestation(entry)

        attest_mock.assert_not_called()

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
        ELECTION_REKOR_RETRY_COUNT=2,
        ELECTION_REKOR_TIMEOUT_SECONDS=5,
    )
    def test_attest_entry_is_idempotent_when_rekor_id_exists(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )
        entry.rekor_log_id = "existing-uuid"
        entry.save(update_fields=["rekor_log_id"])

        with patch("core.elections_timestamping.requests.post") as post_mock:
            _attest_entry(entry)

        post_mock.assert_not_called()

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
        ELECTION_REKOR_RETRY_COUNT=1,
        ELECTION_REKOR_TIMEOUT_SECONDS=5,
    )
    def test_attest_entry_retries_after_network_error(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )

        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {
            "uuid-1": {
                "logIndex": 7,
                "integratedTime": 1_730_000_000,
            }
        }

        with (
            patch("core.elections_timestamping._load_private_key", return_value=_FakePrivateKey()),
            patch(
                "core.elections_timestamping.requests.post",
                side_effect=[requests.Timeout("timeout"), success_response],
            ) as post_mock,
        ):
            _attest_entry(entry)

        entry.refresh_from_db()
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(entry.rekor_log_id, "uuid-1")
        self.assertEqual(entry.rekor_log_index, 7)
        self.assertIsNotNone(entry.rekor_integrated_time)
        self.assertEqual(entry.rekor_endpoint, "https://rekor.example")
        self.assertEqual(entry.rekor_canonical_message_version, 1)
        self.assertEqual(len(str(entry.rekor_message_digest_hex or "")), 64)

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
        ELECTION_REKOR_RETRY_COUNT=0,
        ELECTION_REKOR_TIMEOUT_SECONDS=5,
    )
    def test_attest_entry_treats_http_409_as_success(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )

        conflict_response = Mock()
        conflict_response.status_code = 409
        conflict_response.headers = {
            "Location": "https://rekor.example/api/v1/log/entries/uuid-conflict",
        }
        conflict_response.json.return_value = {}

        with (
            patch("core.elections_timestamping._load_private_key", return_value=_FakePrivateKey()),
            patch("core.elections_timestamping.requests.post", return_value=conflict_response),
        ):
            _attest_entry(entry)

        entry.refresh_from_db()
        self.assertEqual(entry.rekor_log_id, "uuid-conflict")
        self.assertEqual(entry.rekor_endpoint, "https://rekor.example")

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
    )
    def test_schedule_attestation_swallows_registration_exceptions(self) -> None:
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )

        with (
            patch(
                "core.elections_timestamping.transaction.get_connection",
                side_effect=RuntimeError("connection state unavailable"),
            ),
            patch("core.elections_timestamping.logger.exception") as logger_exception,
        ):
            schedule_attestation(entry)

        logger_exception.assert_called_once_with(
            "Rekor attestation scheduling failed for audit entry id=%s",
            entry.id,
        )

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
        ELECTION_REKOR_RETRY_COUNT=0,
        ELECTION_REKOR_TIMEOUT_SECONDS=5,
    )
    def test_attest_entry_sends_correct_hashedrekord_shape(self) -> None:
        """Verifies hashedrekord POST body: algorithm, unprefixed hex digest, base64 sig, base64 PKIX key."""
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )

        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {
            "uuid-shape": {
                "logIndex": 1,
                "integratedTime": 1_730_000_000,
            }
        }

        captured_payload: dict[str, object] = {}

        def _capture_post(
            url: str,
            *,
            json: dict[str, object] | None = None,
            timeout: int | None = None,
            **kwargs: object,
        ) -> Mock:
            del url
            del timeout
            del kwargs
            nonlocal captured_payload
            captured_payload = json or {}
            return success_response

        with (
            patch("core.elections_timestamping._load_private_key", return_value=_FakePrivateKey()),
            patch("core.elections_timestamping.requests.post", side_effect=_capture_post),
        ):
            _attest_entry(entry)

        self.assertEqual(captured_payload.get("kind"), "hashedrekord")
        self.assertEqual(captured_payload.get("apiVersion"), "0.0.1")
        spec = captured_payload.get("spec", {})
        if not isinstance(spec, dict):
            self.fail("expected spec to be a dict")

        data = spec.get("data", {})
        if not isinstance(data, dict):
            self.fail("expected spec.data to be a dict")

        data_hash = data.get("hash", {})
        if not isinstance(data_hash, dict):
            self.fail("expected spec.data.hash to be a dict")

        self.assertEqual(data_hash.get("algorithm"), "sha256")

        hex_value = str(data_hash.get("value", ""))
        self.assertFalse(hex_value.startswith("sha256:"), "hash.value must not have sha256: prefix")
        self.assertEqual(len(hex_value), 64)

        signature = spec.get("signature", {})
        if not isinstance(signature, dict):
            self.fail("expected spec.signature to be a dict")

        sig_b64 = signature.get("content", "")
        import base64 as _b64

        decoded_sig = _b64.b64decode(sig_b64)
        self.assertEqual(decoded_sig, b"fake-signature-der")

        public_key = signature.get("publicKey", {})
        if not isinstance(public_key, dict):
            self.fail("expected spec.signature.publicKey to be a dict")

        pk_b64 = public_key.get("content", "")
        decoded_pk = _b64.b64decode(pk_b64)
        self.assertEqual(decoded_pk, b"fake-public-key")

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
        ELECTION_REKOR_RETRY_COUNT=0,
        ELECTION_REKOR_TIMEOUT_SECONDS=5,
    )
    def test_attest_entry_treats_409_with_body_uuid_as_success(self) -> None:
        """409 with UUID in response body (not Location header) is treated as success."""
        entry = self._create_public_entry(
            event_type="election_closed",
            payload={"chain_head": "abc"},
        )

        conflict_response = Mock()
        conflict_response.status_code = 409
        conflict_response.headers = {}
        conflict_response.json.return_value = {"uuid": "uuid-from-body"}

        with (
            patch("core.elections_timestamping._load_private_key", return_value=_FakePrivateKey()),
            patch("core.elections_timestamping.requests.post", return_value=conflict_response),
        ):
            _attest_entry(entry)

        entry.refresh_from_db()
        self.assertEqual(entry.rekor_log_id, "uuid-from-body")

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
    )
    def test_schedule_attestation_writes_failure_event_after_final_failure(self) -> None:
        election = self._create_election()
        entry = AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={"chain_head": "abc"},
            is_public=True,
        )

        with patch("core.elections_timestamping._attest_entry", side_effect=RuntimeError("boom")):
            with self.captureOnCommitCallbacks(execute=True):
                schedule_attestation(entry)

        self.assertTrue(
            AuditLogEntry.objects.filter(
                election=election,
                event_type="rekor_attestation_failed",
                is_public=True,
            ).exists()
        )

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
    )
    def test_failure_event_payload_contains_only_error_type(self) -> None:
        election = self._create_election()
        entry = AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={"chain_head": "abc"},
            is_public=True,
        )

        with patch("core.elections_timestamping._attest_entry", side_effect=ValueError("sensitive details")):
            with self.captureOnCommitCallbacks(execute=True):
                schedule_attestation(entry)

        failure = AuditLogEntry.objects.get(
            election=election,
            event_type="rekor_attestation_failed",
            is_public=True,
        )
        self.assertEqual(failure.payload, {"error_type": "ValueError"})

    def test_build_public_audit_export_includes_timestamping_block(self) -> None:
        election = self._create_election()
        integrated_time = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={"chain_head": "abc", "actor": "alice"},
            is_public=True,
            rekor_log_id="uuid-123",
            rekor_endpoint="https://rekor.example",
            rekor_log_index=55,
            rekor_integrated_time=integrated_time,
            rekor_message_digest_hex="a" * 64,
            rekor_canonical_message_version=1,
        )

        payload = build_public_audit_export(election=election)
        event = payload["audit_log"][0]

        self.assertEqual(event["payload"], {"chain_head": "abc"})
        self.assertEqual(event["timestamping"]["version"], 1)
        self.assertEqual(event["timestamping"]["rekor_log_id"], "uuid-123")
        self.assertEqual(event["timestamping"]["rekor_log_index"], 55)
        self.assertEqual(event["timestamping"]["rekor_integrated_time"], "2026-01-02T03:04:05Z")
        self.assertEqual(
            event["timestamping"]["rekor_entry_url"],
            "https://rekor.example/api/v1/log/entries/uuid-123",
        )
        self.assertEqual(event["timestamping"]["message_digest_hex"], "a" * 64)
        self.assertEqual(event["timestamping"]["canonical_message_version"], 1)
