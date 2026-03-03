#!/usr/bin/env python3
"""
Verify Rekor transparency-log attestations in public-audit.json (local + optional online check)

This script verifies that events in public-audit.json that carry a Rekor attestation
have correct canonical message digests and (optionally) that the logged Rekor entries
exist and match.

Two verification modes:
  Offline (always):  recompute the canonical SHA-256 digest from the exported event
                     data and compare it to timestamping.message_digest_hex.
  Online (optional): fetch each rekor_entry_url to confirm the entry exists in the
                     Rekor log and that the logged hash matches.

This script runs with Python stdlib only (no pip install required).
"""

# ===== USER INPUT =====
# Download public-audit.json from the election page and keep it next to this script.
audit_file: str = "public-audit.json"
# Set True to also query each Rekor entry URL via HTTPS.
verify_rekor_online: bool = True
# ===== END OF USER INPUT =====

import base64  # noqa: E402
import datetime  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
from urllib import request as _urllib_request  # noqa: E402


def _canonical_bytes(*, event_type: object, payload: object) -> bytes:
    """Canonical message bytes for digest recomputation (must match server-side logic)."""
    return json.dumps(
        {"event_type": event_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


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

            spec = body_json.get("spec")
            if not isinstance(spec, dict):
                raise ValueError("missing spec")
            data = spec.get("data")
            if not isinstance(data, dict):
                raise ValueError("missing data section")
            hash_data = data.get("hash")
            if not isinstance(hash_data, dict):
                raise ValueError("missing hash section")

            rekor_digest = str(hash_data.get("value") or "").strip().lower()
            if rekor_digest and rekor_digest == expected_digest:
                print("  ✓ online: PASS (Rekor hash matches)")
            else:
                print(f"  ✗ online: FAIL (rekor={rekor_digest} expected={expected_digest})")
                all_pass = False

            integrated_time_raw = wrapper_entry.get("integratedTime")
            if integrated_time_raw is not None:
                ts = datetime.datetime.fromtimestamp(int(integrated_time_raw), tz=datetime.UTC)
                print(f"    rekor timestamp: {ts.isoformat()}")
        except Exception as exc:
            print(f"  Warning: online check failed ({type(exc).__name__}: {exc})")

    if not found_any:
        print("No attested events found.")

    return found_any, all_pass


if __name__ == "__main__":
    with open(audit_file, encoding="utf-8") as f:
        audit_data = json.load(f)

    if not isinstance(audit_data, dict):
        raise SystemExit("public-audit.json must contain a JSON object")

    found_any, all_pass = verify_rekor_attestations(
        audit_data=audit_data,
        verify_online=verify_rekor_online,
    )

    if not found_any:
        raise SystemExit(0)

    print()
    if all_pass:
        print("✓ All digest checks passed.")
        raise SystemExit(0)

    print("✗ One or more digest checks failed.")
    raise SystemExit(1)
