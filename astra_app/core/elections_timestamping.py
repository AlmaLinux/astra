from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.db import transaction

from core.logging_extras import current_exception_log_fields
from core.models import AuditLogEntry

logger = logging.getLogger(__name__)

ATTESTED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "election_started",
        "election_end_extended",
        "quorum_reached",
        "election_closed",
        "tally_completed",
    }
)

CANONICAL_MESSAGE_VERSION = 1


def get_public_payload(entry: AuditLogEntry) -> Any:
    if entry.event_type == "rekor_attestation_failed":
        return {}

    payload = entry.payload
    if isinstance(payload, dict):
        if entry.event_type == "election_started":
            public_payload: dict[str, object] = {}
            if "genesis_chain_hash" in payload:
                public_payload["genesis_chain_hash"] = payload["genesis_chain_hash"]
            candidates = payload.get("candidates")
            if isinstance(candidates, list):
                public_payload["candidates"] = [
                    {
                        "id": candidate.get("id"),
                        "freeipa_username": candidate.get("freeipa_username"),
                        "tiebreak_uuid": candidate.get("tiebreak_uuid"),
                    }
                    for candidate in candidates
                    if isinstance(candidate, dict)
                ]
            return public_payload

        public_payload = dict(payload)
        public_payload.pop("actor", None)
        if entry.event_type == "election_closed":
            # Keep final chain head public while withholding sensitive close counters.
            public_payload.pop("credentials_affected", None)
            public_payload.pop("emails_scrubbed", None)
        return public_payload
    return {"data": payload}


def get_canonical_bytes(entry: AuditLogEntry) -> bytes:
    canonical_obj = {
        "event_type": entry.event_type,
        "payload": get_public_payload(entry),
    }
    return json.dumps(canonical_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_private_key() -> ec.EllipticCurvePrivateKey:
    key_value = str(settings.ELECTION_REKOR_SIGNING_KEY or "").strip()
    if not key_value:
        raise ValueError("ELECTION_REKOR_SIGNING_KEY is not configured")

    if key_value.startswith("-----"):
        pem_bytes = key_value.encode("utf-8")
    else:
        with open(key_value, "rb") as key_file:
            pem_bytes = key_file.read()

    loaded_key = serialization.load_pem_private_key(pem_bytes, password=None)
    if not isinstance(loaded_key, ec.EllipticCurvePrivateKey):
        raise TypeError("ELECTION_REKOR_SIGNING_KEY must be an EC private key")
    if not isinstance(loaded_key.curve, ec.SECP256R1):
        raise ValueError("ELECTION_REKOR_SIGNING_KEY must use curve P-256")

    return loaded_key


def _extract_conflict_uuid(response: requests.Response) -> str:
    location = str(response.headers.get("Location") or "").strip()
    if location:
        return location.rstrip("/").split("/")[-1]

    body = response.json()
    if isinstance(body, dict):
        uuid_value = body.get("uuid")
        if isinstance(uuid_value, str) and uuid_value.strip():
            return uuid_value.strip()

        location_value = body.get("Location")
        if isinstance(location_value, str) and location_value.strip():
            return location_value.rstrip("/").split("/")[-1]

        if len(body) == 1:
            wrapper_uuid = next(iter(body.keys()))
            if isinstance(wrapper_uuid, str) and wrapper_uuid.strip():
                return wrapper_uuid.strip()

    raise ValueError("Unable to determine Rekor UUID from 409 response")


def _attest_entry(entry: AuditLogEntry) -> None:
    if entry.rekor_log_id:
        return

    canonical_bytes = get_canonical_bytes(entry)
    digest_hex = hashlib.sha256(canonical_bytes).hexdigest()

    private_key = _load_private_key()
    signature_der_bytes = private_key.sign(canonical_bytes, ec.ECDSA(hashes.SHA256()))
    # Rekor hashedrekord requires base64(PEM), not base64(DER), for the public key.
    pubkey_pem_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    payload: dict[str, object] = {
        "apiVersion": "0.0.1",
        "kind": "hashedrekord",
        "spec": {
            "data": {
                "hash": {
                    "algorithm": "sha256",
                    "value": digest_hex,
                }
            },
            "signature": {
                "content": base64.b64encode(signature_der_bytes).decode("ascii"),
                "publicKey": {
                    "content": base64.b64encode(pubkey_pem_bytes).decode("ascii"),
                },
            },
        },
    }

    endpoint = str(settings.ELECTION_REKOR_ENDPOINT or "").strip().rstrip("/")
    timeout_seconds = int(settings.ELECTION_REKOR_TIMEOUT_SECONDS)
    total_attempts = int(settings.ELECTION_REKOR_RETRY_COUNT) + 1

    last_error: Exception | None = None
    for _ in range(max(total_attempts, 1)):
        try:
            response = requests.post(
                f"{endpoint}/api/v1/log/entries",
                json=payload,
                timeout=timeout_seconds,
            )

            rekor_log_id: str
            rekor_log_index: int | None = None
            rekor_integrated_time: datetime | None = None

            if response.status_code == 201:
                response_body = response.json()
                if not isinstance(response_body, dict) or not response_body:
                    raise ValueError("Unexpected Rekor 201 response shape")

                rekor_log_id = str(next(iter(response_body.keys())))
                wrapper = response_body[rekor_log_id]
                if not isinstance(wrapper, dict):
                    raise ValueError("Unexpected Rekor 201 wrapper shape")

                log_index_raw = wrapper.get("logIndex")
                if log_index_raw is not None:
                    rekor_log_index = int(log_index_raw)

                integrated_time_raw = wrapper.get("integratedTime")
                if integrated_time_raw is not None:
                    rekor_integrated_time = datetime.fromtimestamp(int(integrated_time_raw), tz=UTC)
            elif response.status_code == 409:
                rekor_log_id = _extract_conflict_uuid(response)
            else:
                response.raise_for_status()
                raise ValueError(f"Unexpected Rekor response status: {response.status_code}")

            entry.rekor_log_id = rekor_log_id
            entry.rekor_endpoint = endpoint
            entry.rekor_log_index = rekor_log_index
            entry.rekor_integrated_time = rekor_integrated_time
            entry.rekor_message_digest_hex = digest_hex
            entry.rekor_canonical_message_version = CANONICAL_MESSAGE_VERSION
            entry.save(
                update_fields=[
                    "rekor_log_id",
                    "rekor_endpoint",
                    "rekor_log_index",
                    "rekor_integrated_time",
                    "rekor_message_digest_hex",
                    "rekor_canonical_message_version",
                ]
            )
            return
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error


def _write_attestation_failed(entry_id: int, error_type: str) -> None:
    try:
        source_entry = AuditLogEntry.objects.only("election_id", "organization_id").get(pk=entry_id)
        AuditLogEntry.objects.create(
            election_id=source_entry.election_id,
            organization_id=source_entry.organization_id,
            event_type="rekor_attestation_failed",
            is_public=True,
            payload={"error_type": str(error_type)},
        )
    except Exception:
        pass


def schedule_attestation(entry: AuditLogEntry) -> None:
    endpoint = str(settings.ELECTION_REKOR_ENDPOINT or "").strip()
    signing_key = str(settings.ELECTION_REKOR_SIGNING_KEY or "").strip()

    if not endpoint or not signing_key:
        return
    if entry.event_type not in ATTESTED_EVENT_TYPES:
        return

    def _on_commit() -> None:
        try:
            try:
                _attest_entry(entry)
            except Exception as exc:
                logger.exception(
                    "Rekor attestation failed for audit entry id=%s",
                    entry.id,
                    extra=current_exception_log_fields(),
                )
                try:
                    _write_attestation_failed(entry_id=entry.id, error_type=type(exc).__name__)
                except Exception:
                    pass
        except Exception:
            pass

    try:
        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(_on_commit)
        else:
            _on_commit()
    except Exception:
        logger.exception(
            "Rekor attestation scheduling failed for audit entry id=%s",
            entry.id,
            extra=current_exception_log_fields(),
        )
