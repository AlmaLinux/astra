#!/usr/bin/env python3
"""
Verify Rekor transparency-log attestations in public-audit.json (local + optional online trust check)

This script verifies that events in public-audit.json that carry a Rekor attestation
have correct canonical message digests and (optionally) that the logged Rekor entries
exist and match.

This script validates the audit and attestation record only. It does not prove that
public-ballots.json is the matching ballot-ledger publication pair.

Two verification modes:
    Offline (always):  recompute the canonical SHA-256 digest from the exported event
                                         data and compare it to timestamping.message_digest_hex.
    Online (optional): fetch each rekor_entry_url, confirm the entry exists in the
                                         Rekor log, verify the hashedrekord signature over the canonical
                                         bytes, and require the embedded attestation signer to match the
                                         trusted public key or fingerprint configured below.

The hardcoded trusted key material below is the default trust root for the attestation
signer embedded in the Rekor entry payload. It is not Rekor's own transparency-log key.

Online attestation-signer verification uses the local OpenSSL executable.
"""

# ===== USER INPUT =====
# Download public-audit.json from the election page and keep it next to this script.
audit_file: str = "public-audit.json"
# Set False to not query each Rekor entry URL via HTTPS.
verify_rekor_online: bool = True
# Trusted attestation signer for the environment whose Rekor entry is being
# audited. This is the signer embedded in the attestation payload, not Rekor's
# transparency-log key. Replace these only when auditing a different signer.
trusted_public_key_pem: str = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE6VF2PVPmOMIg5eRV1+MIK/hRXy53
4wHhKn77HEEZP5mfbkOtEkVEVsO8W4X0dsubDOlAcx49ckaAH/KGMsHPkQ==
-----END PUBLIC KEY-----"""
trusted_public_key_sha256: str = "99f3b7b90a6d81ac36e8aaf8066d3da0d7ccd49dab06995ad6eeb83384a0dd12"
# Require Rekor integrated time to be within this many seconds of the original
# exported audit-entry timestamp before v2 verification can reach `valid`.
rekor_integrated_time_tolerance_seconds: int = 5
# ===== END OF USER INPUT =====

CONFIG_MANIFEST_VERSION = 1

import base64  # noqa: E402
import datetime  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from urllib import request as _urllib_request  # noqa: E402


def _canonical_bytes(*, event_type: object, payload: object) -> bytes:
    """Canonical message bytes for digest recomputation (must match server-side logic)."""
    return json.dumps(
        {"event_type": event_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _compute_chain_anchor_hash(*, election_id: int, config_manifest_sha256: str) -> str:
    data = (
        f"election-v2:{election_id}:{config_manifest_sha256}. "
        "alex estuvo aquí, dejándose el alma."
    ).encode()
    return hashlib.sha256(data).hexdigest()


def _normalized_sha256_hex(value: object) -> str:
    return str(value or "").strip().lower()


def _resolve_payload_genesis_hash(*, payload: dict[str, object], label: str) -> str:
    canonical_genesis = _normalized_sha256_hex(payload.get("genesis_hash"))
    legacy_anchor = _normalized_sha256_hex(payload.get("chain_anchor_hash"))
    legacy_genesis = _normalized_sha256_hex(payload.get("genesis_chain_hash"))
    legacy_root = _normalized_sha256_hex(payload.get("chain_root_hash"))
    if canonical_genesis and legacy_anchor and canonical_genesis != legacy_anchor:
        raise ValueError(f"{label} chain_anchor_hash alias does not match genesis_hash")
    if canonical_genesis and legacy_genesis and canonical_genesis != legacy_genesis:
        raise ValueError(f"{label} genesis_chain_hash alias does not match genesis_hash")
    if canonical_genesis and legacy_root and canonical_genesis != legacy_root:
        raise ValueError(f"{label} chain_root_hash alias does not match genesis_hash")
    resolved = canonical_genesis or legacy_anchor or legacy_genesis or legacy_root
    if not resolved:
        raise ValueError(f"{label} missing genesis_hash")
    return resolved


def _require_supported_manifest_version(*, value: object, label: str) -> int:
    if value in (None, ""):
        raise ValueError(f"{label} missing config_manifest_version")
    try:
        manifest_version = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} config_manifest_version must be an integer") from exc
    if manifest_version != CONFIG_MANIFEST_VERSION:
        raise ValueError(
            f"{label} config_manifest_version must be {CONFIG_MANIFEST_VERSION} for v2 verification"
        )
    return manifest_version


def _require_manifest_dict_field(*, data: dict[str, object], field_name: str, label: str) -> dict[str, object]:
    value = data.get(field_name)
    if not isinstance(value, dict):
        raise ValueError(f"{label} missing {field_name} section")
    return value


def _require_manifest_list_field(*, data: dict[str, object], field_name: str, label: str) -> list[object]:
    value = data.get(field_name)
    if not isinstance(value, list):
        raise ValueError(f"{label} {field_name} must be a list")
    return value


def _require_manifest_int(*, data: dict[str, object], field_name: str, label: str) -> int:
    value = data.get(field_name)
    if isinstance(value, bool):
        raise ValueError(f"{label} {field_name} must be an integer")
    if not isinstance(value, int):
        raise ValueError(f"{label} {field_name} must be an integer")
    return value


def _require_manifest_str(*, data: dict[str, object], field_name: str, label: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"{label} {field_name} must be a string")
    return value


def _validate_manifest_v1_schema(*, manifest: dict[str, object]) -> None:
    election = _require_manifest_dict_field(data=manifest, field_name="election", label="config_manifest")
    tally_rule = _require_manifest_dict_field(data=manifest, field_name="tally_rule", label="config_manifest")
    candidates = _require_manifest_list_field(data=manifest, field_name="candidates", label="config_manifest")
    exclusion_groups = _require_manifest_list_field(
        data=manifest,
        field_name="exclusion_groups",
        label="config_manifest",
    )

    _require_manifest_int(data=election, field_name="id", label="config_manifest election")
    for field_name in (
        "name",
        "start_datetime",
        "eligible_group_cn",
    ):
        _require_manifest_str(data=election, field_name=field_name, label="config_manifest election")
    for field_name in ("number_of_seats", "quorum"):
        _require_manifest_int(data=election, field_name=field_name, label="config_manifest election")

    for field_name in ("algorithm", "algorithm_version", "spec_identity", "epsilon"):
        _require_manifest_str(data=tally_rule, field_name=field_name, label="config_manifest tally_rule")
    _require_manifest_int(data=tally_rule, field_name="max_iterations", label="config_manifest tally_rule")

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"config_manifest candidates[{index}] must be an object")
        _require_manifest_int(data=candidate, field_name="id", label=f"config_manifest candidates[{index}]")
        for field_name in (
            "freeipa_username",
            "nominated_by",
            "tiebreak_uuid",
        ):
            _require_manifest_str(
                data=candidate,
                field_name=field_name,
                label=f"config_manifest candidates[{index}]",
            )

    for index, group in enumerate(exclusion_groups):
        if not isinstance(group, dict):
            raise ValueError(f"config_manifest exclusion_groups[{index}] must be an object")
        for field_name in ("public_id", "name"):
            _require_manifest_str(
                data=group,
                field_name=field_name,
                label=f"config_manifest exclusion_groups[{index}]",
            )
        _require_manifest_int(
            data=group,
            field_name="max_elected",
            label=f"config_manifest exclusion_groups[{index}]",
        )
        candidate_ids = _require_manifest_list_field(
            data=group,
            field_name="candidate_ids",
            label=f"config_manifest exclusion_groups[{index}]",
        )
        for candidate_index, candidate_id in enumerate(candidate_ids):
            if isinstance(candidate_id, bool) or not isinstance(candidate_id, int):
                raise ValueError(
                    "config_manifest exclusion_groups"
                    f"[{index}] candidate_ids[{candidate_index}] must be an integer"
                )


def _format_utc(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")


def _parse_precise_utc_timestamp(value: object) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text or "T" not in text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.UTC)


def _resolve_exported_event_timestamp(event: dict[str, object]) -> datetime.datetime | None:
    precise_timestamp = _parse_precise_utc_timestamp(event.get("timestamp_utc"))
    if precise_timestamp is not None:
        return precise_timestamp
    return _parse_precise_utc_timestamp(event.get("timestamp"))


def _load_user_public_key_pem_bytes(configured_value: str) -> bytes:
    stripped = configured_value.strip()
    if not stripped:
        return b""
    if stripped.startswith("-----BEGIN"):
        return f"{stripped.rstrip()}\n".encode()
    with open(stripped, "rb") as key_file:
        return key_file.read()


def _openssl_public_key_fingerprint_sha256(*, pem_bytes: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        public_key_path = f"{tmpdir}/public-key.pem"
        with open(public_key_path, "wb") as public_key_file:
            public_key_file.write(pem_bytes)

        completed = subprocess.run(
            ["openssl", "pkey", "-pubin", "-inform", "PEM", "-outform", "DER", "-in", public_key_path],
            check=False,
            capture_output=True,
        )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"OpenSSL failed to parse public key: {stderr or 'unknown error'}")
    return hashlib.sha256(completed.stdout).hexdigest()


def _verify_signature_with_public_key(*, pem_bytes: bytes, signature_bytes: bytes, canonical_bytes: bytes) -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        public_key_path = f"{tmpdir}/public-key.pem"
        signature_path = f"{tmpdir}/signature.der"
        message_path = f"{tmpdir}/message.bin"
        with open(public_key_path, "wb") as public_key_file:
            public_key_file.write(pem_bytes)
        with open(signature_path, "wb") as signature_file:
            signature_file.write(signature_bytes)
        with open(message_path, "wb") as message_file:
            message_file.write(canonical_bytes)

        completed = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                public_key_path,
                "-signature",
                signature_path,
                message_path,
            ],
            check=False,
            capture_output=True,
        )
    if completed.returncode != 0:
        return False
    return b"Verified OK" in completed.stdout


def _resolve_trusted_public_key_fingerprint(*, trusted_pem_value: str, trusted_fingerprint_value: str) -> str:
    trusted_pem_bytes = _load_user_public_key_pem_bytes(trusted_pem_value)
    configured_fingerprint = _normalized_sha256_hex(trusted_fingerprint_value)
    pem_fingerprint = ""
    if trusted_pem_bytes:
        pem_fingerprint = _openssl_public_key_fingerprint_sha256(pem_bytes=trusted_pem_bytes)

    if configured_fingerprint and pem_fingerprint and configured_fingerprint != pem_fingerprint:
        raise ValueError("trusted public key fingerprint does not match trusted public key input")

    return configured_fingerprint or pem_fingerprint


def _decode_rekor_entry(*, rekor_entry_url: str) -> tuple[dict[str, object], dict[str, object]]:
    with _urllib_request.urlopen(rekor_entry_url, timeout=10) as _resp:
        wrapper_json = json.loads(_resp.read().decode("utf-8"))

    if not isinstance(wrapper_json, dict) or len(wrapper_json) != 1:
        raise ValueError(
            "unexpected Rekor response shape "
            f"({len(wrapper_json) if isinstance(wrapper_json, dict) else type(wrapper_json).__name__} keys)"
        )

    wrapper_entry = next(iter(wrapper_json.values()))
    if not isinstance(wrapper_entry, dict):
        raise ValueError("invalid Rekor entry object")

    body_b64 = wrapper_entry.get("body")
    if not isinstance(body_b64, str) or not body_b64.strip():
        raise ValueError("missing Rekor body field")

    body_json = json.loads(base64.b64decode(body_b64).decode("utf-8"))
    if not isinstance(body_json, dict):
        raise ValueError("invalid decoded body")

    return wrapper_entry, body_json


def _verify_online_rekor_entry(
    *,
    rekor_entry_url: str,
    expected_digest: str,
    canonical_bytes: bytes,
    trusted_pem_value: str,
    trusted_fingerprint_value: str,
) -> dict[str, object]:
    wrapper_entry, body_json = _decode_rekor_entry(rekor_entry_url=rekor_entry_url)

    spec = body_json.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("missing spec")
    data = spec.get("data")
    if not isinstance(data, dict):
        raise ValueError("missing data section")
    hash_data = data.get("hash")
    if not isinstance(hash_data, dict):
        raise ValueError("missing hash section")

    rekor_digest = _normalized_sha256_hex(hash_data.get("value"))

    signature = spec.get("signature")
    if not isinstance(signature, dict):
        raise ValueError("missing signature section")
    signature_b64 = signature.get("content")
    if not isinstance(signature_b64, str) or not signature_b64.strip():
        raise ValueError("missing signature content")
    signature_bytes = base64.b64decode(signature_b64)

    public_key = signature.get("publicKey")
    if not isinstance(public_key, dict):
        raise ValueError("missing publicKey section")
    public_key_b64 = public_key.get("content")
    if not isinstance(public_key_b64, str) or not public_key_b64.strip():
        raise ValueError("missing publicKey content")
    embedded_public_key_pem = base64.b64decode(public_key_b64)
    embedded_public_key_sha256 = _openssl_public_key_fingerprint_sha256(pem_bytes=embedded_public_key_pem)
    resolved_trusted_fingerprint = _resolve_trusted_public_key_fingerprint(
        trusted_pem_value=trusted_pem_value,
        trusted_fingerprint_value=trusted_fingerprint_value,
    )
    integrated_time_value = wrapper_entry.get("integratedTime")
    if integrated_time_value in (None, ""):
        raise ValueError("missing Rekor integratedTime")
    try:
        integrated_time = datetime.datetime.fromtimestamp(int(integrated_time_value), tz=datetime.UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("invalid Rekor integratedTime") from exc

    return {
        "rekor_digest": rekor_digest,
        "digest_matches": rekor_digest == expected_digest,
        "signature_valid": _verify_signature_with_public_key(
            pem_bytes=embedded_public_key_pem,
            signature_bytes=signature_bytes,
            canonical_bytes=canonical_bytes,
        ),
        "embedded_public_key_sha256": embedded_public_key_sha256,
        "trusted_public_key_sha256": resolved_trusted_fingerprint,
        "trusted_configured": bool(resolved_trusted_fingerprint),
        "signer_trusted": bool(resolved_trusted_fingerprint)
        and embedded_public_key_sha256 == resolved_trusted_fingerprint,
        "integrated_time": integrated_time,
        "integrated_time_utc": _format_utc(integrated_time),
    }


def verify_rekor_attestations(*, audit_data: dict[str, object], verify_online: bool) -> tuple[bool, bool]:
    print("Rekor Attestation Verification")
    print("=" * 60)

    audit_log = audit_data.get("audit_log")
    if not isinstance(audit_log, list):
        print("Warning: public-audit.json missing audit_log list")
        return False, False

    found_any = False
    all_pass = True

    for event in audit_log:
        if not isinstance(event, dict):
            print("Warning: skipping non-object audit event")
            continue

        timestamping = event.get("timestamping")
        if not isinstance(timestamping, dict):
            continue
        if timestamping.get("canonical_message_version") != 1:
            continue

        found_any = True
        event_type = str(event.get("event_type") or "unknown")
        rekor_entry_url = str(timestamping.get("rekor_entry_url") or "").strip()
        expected_digest = str(timestamping.get("message_digest_hex") or "").strip().lower()

        print()  # blank line between events
        print(f"- event_type: {event_type}")
        print(f"  rekor_entry_url: {rekor_entry_url or '<missing>'}")

        try:
            computed_digest = hashlib.sha256(
                _canonical_bytes(event_type=event.get("event_type"), payload=event.get("payload"))
            ).hexdigest()
        except Exception as exc:
            print(f"  ✗ digest: error ({type(exc).__name__})")
            all_pass = False
            continue

        if expected_digest and computed_digest == expected_digest:
            print(f"  ✓ digest: PASS ({computed_digest})")
        else:
            print(f"  ✗ digest: FAIL (computed={computed_digest} expected={expected_digest})")
            all_pass = False

        if not verify_online:
            continue

        if not rekor_entry_url:
            print("  Warning: online check skipped (missing rekor_entry_url)")
            continue

        try:
            online_result = _verify_online_rekor_entry(
                rekor_entry_url=rekor_entry_url,
                expected_digest=expected_digest,
                canonical_bytes=_canonical_bytes(event_type=event.get("event_type"), payload=event.get("payload")),
                trusted_pem_value="",
                trusted_fingerprint_value="",
            )
            if online_result["digest_matches"]:
                print("  ✓ online inclusion: PASS (digest-scoped Rekor entry matches exported canonical digest)")
            else:
                print(
                    "  ✗ online inclusion: FAIL "
                    f"(rekor={online_result['rekor_digest']} expected={expected_digest})"
                )
                all_pass = False
            if online_result["signature_valid"]:
                print("  ✓ online signature: PASS (Rekor entry signature validates)")
            else:
                print("  ✗ online signature: FAIL")
                all_pass = False
            print(
                "  ! signer trust scope: generic event checks do not promote trust; "
                "v2 election definition trust is evaluated separately."
            )
        except Exception as exc:
            print(f"  Warning: online check failed ({type(exc).__name__}: {exc})")

    if not found_any:
        print("No attested events found.")

    return found_any, all_pass


def evaluate_v2_election_definition(
    *,
    audit_data: dict[str, object],
    verify_online: bool,
    trusted_public_key_pem: str | None = None,
    trusted_public_key_sha256: str | None = None,
    rekor_time_tolerance_seconds: int = rekor_integrated_time_tolerance_seconds,
) -> dict[str, object]:
    audit_log = audit_data.get("audit_log")
    if not isinstance(audit_log, list):
        return {"status": "invalid", "reason": "public-audit.json missing audit_log list"}

    election_started = None
    for event in audit_log:
        if isinstance(event, dict) and str(event.get("event_type") or "") == "election_started":
            election_started = event
            break

    if election_started is None:
        return {"status": "not_applicable"}

    payload = election_started.get("payload")
    timestamping = election_started.get("timestamping")
    if not isinstance(payload, dict):
        return {"status": "invalid", "reason": "election_started payload must be an object"}
    if not isinstance(timestamping, dict):
        return {"status": "invalid", "reason": "election_started missing timestamping metadata"}
    if int(payload.get("chain_version") or 1) != 2:
        return {"status": "not_applicable"}
    if timestamping.get("canonical_message_version") != 1:
        return {"status": "invalid", "reason": "election_started canonical_message_version mismatch"}

    expected_digest = str(timestamping.get("message_digest_hex") or "").strip().lower()
    canonical_bytes = _canonical_bytes(event_type=election_started.get("event_type"), payload=payload)
    computed_digest = hashlib.sha256(canonical_bytes).hexdigest()
    if computed_digest != expected_digest:
        return {"status": "invalid", "reason": "election_started digest verification failed"}

    manifest = payload.get("config_manifest")
    if not isinstance(manifest, dict):
        return {"status": "invalid", "reason": "election_started missing config_manifest"}
    try:
        payload_manifest_version = _require_supported_manifest_version(
            value=payload.get("config_manifest_version"),
            label="election_started payload",
        )
        manifest_version = _require_supported_manifest_version(
            value=manifest.get("version"),
            label="config_manifest",
        )
    except ValueError as exc:
        return {"status": "invalid", "reason": str(exc)}
    if payload_manifest_version != manifest_version:
        return {
            "status": "invalid",
            "reason": "config_manifest_version mismatch between election_started payload and config_manifest",
        }
    try:
        _validate_manifest_v1_schema(manifest=manifest)
    except ValueError as exc:
        return {"status": "invalid", "reason": str(exc)}

    election_payload = manifest.get("election")
    if not isinstance(election_payload, dict):
        return {"status": "invalid", "reason": "config_manifest missing election section"}

    try:
        election_id = int(election_payload.get("id"))
    except (TypeError, ValueError):
        return {"status": "invalid", "reason": "config_manifest election.id must be an integer"}

    manifest_digest = hashlib.sha256(_canonical_manifest_bytes(manifest)).hexdigest()
    exported_manifest_digest = str(payload.get("config_manifest_sha256") or "").strip().lower()
    if manifest_digest != exported_manifest_digest:
        return {"status": "invalid", "reason": "config_manifest_sha256 mismatch"}

    anchor_hash = _compute_chain_anchor_hash(
        election_id=election_id,
        config_manifest_sha256=manifest_digest,
    )
    exported_genesis_hash = _resolve_payload_genesis_hash(payload=payload, label="election_started payload")
    if anchor_hash != exported_genesis_hash:
        return {"status": "invalid", "reason": "genesis_hash mismatch"}

    result = {
        "status": "untrusted_local_only",
        "chain_version": 2,
        "config_manifest_version": payload_manifest_version,
        "config_manifest_sha256": manifest_digest,
        "genesis_hash": anchor_hash,
    }

    if not verify_online:
        return result

    rekor_entry_url = str(timestamping.get("rekor_entry_url") or "").strip()
    if not rekor_entry_url:
        return {"status": "invalid", "reason": "missing rekor_entry_url for online verification"}

    if trusted_public_key_pem is None and trusted_public_key_sha256 is None:
        resolved_trusted_public_key_pem = globals()["trusted_public_key_pem"]
        resolved_trusted_public_key_sha256 = globals()["trusted_public_key_sha256"]
    else:
        resolved_trusted_public_key_pem = trusted_public_key_pem or ""
        resolved_trusted_public_key_sha256 = trusted_public_key_sha256 or ""

    try:
        online_result = _verify_online_rekor_entry(
            rekor_entry_url=rekor_entry_url,
            expected_digest=expected_digest,
            canonical_bytes=canonical_bytes,
            trusted_pem_value=resolved_trusted_public_key_pem,
            trusted_fingerprint_value=resolved_trusted_public_key_sha256,
        )
    except Exception as exc:
        return {"status": "invalid", "reason": f"online Rekor verification failed: {type(exc).__name__}: {exc}"}

    if not online_result["digest_matches"]:
        return {
            "status": "invalid",
            "reason": f"Rekor inclusion digest mismatch: {online_result['rekor_digest']}",
        }

    result["embedded_public_key_sha256"] = str(online_result["embedded_public_key_sha256"])
    result["rekor_integrated_time"] = str(online_result["integrated_time_utc"])

    if not online_result["signature_valid"]:
        return {"status": "invalid", "reason": "Rekor signature verification failed"}

    if not online_result["trusted_configured"]:
        result["reason"] = "expected attestation signer PEM or fingerprint is not configured"
        return result

    result["trusted_public_key_sha256"] = str(online_result["trusted_public_key_sha256"])
    if not online_result["signer_trusted"]:
        return {
            "status": "invalid",
            "reason": (
                "embedded attestation signer fingerprint does not match trusted attestation signer "
                f"(embedded={online_result['embedded_public_key_sha256']} "
                f"trusted={online_result['trusted_public_key_sha256']})"
            ),
        }

    exported_event_timestamp = _resolve_exported_event_timestamp(election_started)
    if exported_event_timestamp is None:
        result["reason"] = (
            "exported audit timestamp lacks sub-day precision; "
            "Rekor timestamp consistency was not checked"
        )
        return result

    timestamp_delta_seconds = abs(
        int((online_result["integrated_time"] - exported_event_timestamp).total_seconds())
    )
    result["event_timestamp_utc"] = _format_utc(exported_event_timestamp)
    result["rekor_timestamp_delta_seconds"] = timestamp_delta_seconds
    result["rekor_time_tolerance_seconds"] = int(rekor_time_tolerance_seconds)
    if timestamp_delta_seconds > int(rekor_time_tolerance_seconds):
        return {
            "status": "invalid",
            "reason": (
                "Rekor integrated timestamp is outside the allowed tolerance "
                f"(delta={timestamp_delta_seconds}s tolerance={int(rekor_time_tolerance_seconds)}s)"
            ),
        }

    result["status"] = "valid"
    return result


if __name__ == "__main__":
    with open(audit_file, encoding="utf-8") as f:
        audit_data = json.load(f)

    if not isinstance(audit_data, dict):
        raise SystemExit("public-audit.json must contain a JSON object")

    found_any, all_pass = verify_rekor_attestations(
        audit_data=audit_data,
        verify_online=verify_rekor_online,
    )

    v2_result = evaluate_v2_election_definition(
        audit_data=audit_data,
        verify_online=verify_rekor_online,
    )

    if v2_result.get("status") != "not_applicable":
        print()
        print("Election Definition Verification")
        print("=" * 60)
        print(f"status: {v2_result.get('status')}")
        if "reason" in v2_result:
            print(f"reason: {v2_result['reason']}")
            if v2_result["reason"] == "expected attestation signer PEM or fingerprint is not configured":
                print(
                    "trust_configuration: no trusted attestation signer is configured; "
                    "set trusted_public_key_pem / trusted_public_key_sha256 for the environment "
                    "whose attestation signer you expect"
                )
        if "chain_version" in v2_result:
            print(f"chain_version: {v2_result['chain_version']}")
            print(f"config_manifest_version: {v2_result['config_manifest_version']}")
            print(f"config_manifest_sha256: {v2_result['config_manifest_sha256']}")
            print(f"genesis_hash: {v2_result['genesis_hash']}")
        if "embedded_public_key_sha256" in v2_result:
            print(f"embedded_public_key_sha256: {v2_result['embedded_public_key_sha256']}")
        if "trusted_public_key_sha256" in v2_result:
            print(f"trusted_public_key_sha256: {v2_result['trusted_public_key_sha256']}")
        if "event_timestamp_utc" in v2_result:
            print(f"event_timestamp_utc: {v2_result['event_timestamp_utc']}")
        if "rekor_integrated_time" in v2_result:
            print(f"rekor_integrated_time: {v2_result['rekor_integrated_time']}")
        if "rekor_timestamp_delta_seconds" in v2_result:
            print(f"rekor_timestamp_delta_seconds: {v2_result['rekor_timestamp_delta_seconds']}")
            print(f"rekor_time_tolerance_seconds: {v2_result['rekor_time_tolerance_seconds']}")

    if not found_any:
        if v2_result.get("status") == "invalid":
            raise SystemExit(1)
        if v2_result.get("status") == "untrusted_local_only":
            raise SystemExit(2)
        raise SystemExit(0)

    print()
    if v2_result.get("status") == "invalid":
        print("✗ Election definition verification failed.")
        raise SystemExit(1)

    if all_pass and v2_result.get("status") not in {"untrusted_local_only"}:
        print("✓ All digest checks passed.")
        raise SystemExit(0)

    if all_pass and v2_result.get("status") == "untrusted_local_only":
        print("! Local digest checks passed, but Rekor inclusion was not independently confirmed.")
        raise SystemExit(2)

    print("✗ One or more digest checks failed.")
    raise SystemExit(1)
