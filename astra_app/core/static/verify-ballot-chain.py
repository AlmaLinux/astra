#!/usr/bin/env python3
"""
Verify the public ballot chain (local check)

This script checks that the downloaded ballots file forms an unbroken SHA-256 chain
and that the computed final election chain head matches the one published for the election.
For chain_version 2 elections, it also requires the matching public-audit.json file
from the same publication pair so it can verify the manifest-to-anchor binding.
It also checks whether your ballot receipt code appears in the file and, when provided,
verifies the receipt-time previous/current ledger hashes against the located ballot row.

This script runs locally and does not contact the election server.

Algorithm: SHA-256 chaining (same as astra_app/core/tokens.py election_chain_next_hash)

Source-of-truth (stable permalinks):
- election_genesis_chain_hash: https://github.com/AlmaLinux/astra/blob/8806e7916ec58df46a7d9f333a2e50baac31bdb7/astra_app/core/tokens.py
- election_chain_next_hash:   https://github.com/AlmaLinux/astra/blob/8806e7916ec58df46a7d9f333a2e50baac31bdb7/astra_app/core/tokens.py
"""

# ===== YOUR BALLOT DETAILS =====
# Copy/paste these values from the same labels shown in the Astra UI and email.

# Find this on the ballot verification page for the election, or on the election page URL/export.
election_id = 1
# Ballot receipt code: find this in the vote receipt email or on the ballot verification page.
ballot_receipt_code = "your-ballot-receipt-code"
# Submission nonce: find this in the vote receipt email or in Advanced receipt info on the ballot verification page.
# This script does not need it, but keeping it here makes it easier to confirm you copied the right receipt.
submission_nonce = "your-submission-nonce"  # Optional
# Receipt previous ledger hash: find this in the vote receipt email or in Advanced receipt info on the ballot verification page.
receipt_previous_ledger_hash = "previous-ledger-hash-from-receipt"  # Optional
# Receipt current ledger hash: find this in the vote receipt email or in Advanced receipt info on the ballot verification page.
receipt_current_ledger_hash = "current-ledger-hash-from-receipt"  # Optional
# Final election chain head / current ledger hash: find this on the election page after the election closes,
# or in the public-ballots.json export chain_head field.
final_election_chain_head = "current-ledger-hash-from-election-page"  # Optional

# Download public-ballots.json from the election page export and keep it next to this script.
ballots_file = "public-ballots.json"
# For v2 elections, download public-audit.json from the same published pair and keep it next to this script.
audit_file = "public-audit.json"
# This script does not contact Rekor directly. Set this to True if you also want the CLI output to remind
# you to run verify-audit-log.py with online verification for the matching public-audit.json file.
verify_rekor_online = False
# ===== END OF USER INPUT =====

CONFIG_MANIFEST_VERSION = 1


import hashlib  # noqa: E402
import json  # noqa: E402
from typing import cast  # noqa: E402


def _clean_optional_text(value: object) -> str:
    return str(value or "").strip()


def _require_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc


def _resolve_user_hash_inputs(
    *,
    previous_ledger_hash: str,
    current_ledger_hash: str,
    receipt_previous_ledger_hash: str,
    receipt_current_ledger_hash: str,
    final_election_chain_head: str,
) -> dict[str, str]:
    return {
        "receipt_previous_ledger_hash": _clean_optional_text(receipt_previous_ledger_hash)
        or _clean_optional_text(previous_ledger_hash),
        "receipt_current_ledger_hash": _clean_optional_text(receipt_current_ledger_hash),
        "final_election_chain_head": _clean_optional_text(final_election_chain_head)
        or _clean_optional_text(current_ledger_hash),
    }


def compute_genesis_hash(election_id: int) -> str:
    """Genesis hash for this election (must match core.tokens.election_genesis_chain_hash)."""
    data = f"election:{election_id}. alex estuvo aquí, dejándose el alma.".encode()
    return hashlib.sha256(data).hexdigest()


def compute_chain_anchor_hash(*, election_id: int, config_manifest_sha256: str) -> str:
    data = (
        f"election-v2:{election_id}:{config_manifest_sha256}. "
        "alex estuvo aquí, dejándose el alma."
    ).encode()
    return hashlib.sha256(data).hexdigest()


def canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_chain_hash(*, previous_chain_hash: str, ballot_hash: str) -> str:
    """Next chain hash (must match core.tokens.election_chain_next_hash)."""
    return hashlib.sha256(f"{previous_chain_hash}:{ballot_hash}".encode()).hexdigest()


def reconstruct_chain_order(
    *,
    ballots: list[dict[str, object]],
    genesis_hash: str,
    origin_label: str = "genesis",
) -> list[dict[str, object]]:
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
        if origin_label == "genesis":
            raise ValueError("missing genesis linkage: no ballot references the election genesis hash")
        raise ValueError(f"missing {origin_label} linkage: no ballot references the {origin_label}")

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


def _require_matching_metadata(*, ballots_export: dict[str, object], audit_export: dict[str, object], key: str) -> str:
    ballots_value = str(ballots_export.get(key) or "").strip()
    audit_value = str(audit_export.get(key) or "").strip()
    if ballots_value != audit_value:
        raise ValueError(f"{key} mismatch between public-ballots.json and public-audit.json")
    return ballots_value


def _resolve_export_genesis_hash(*, export: dict[str, object], label: str) -> str:
    canonical_genesis = _clean_optional_text(export.get("genesis_hash")).lower()
    legacy_anchor = _clean_optional_text(export.get("chain_anchor_hash")).lower()
    legacy_root = _clean_optional_text(export.get("chain_root_hash")).lower()
    if canonical_genesis and legacy_anchor and canonical_genesis != legacy_anchor:
        raise ValueError(f"{label} chain_anchor_hash alias does not match genesis_hash")
    if canonical_genesis and legacy_root and canonical_genesis != legacy_root:
        raise ValueError(f"{label} chain_root_hash alias does not match genesis_hash")
    if legacy_anchor and legacy_root and legacy_anchor != legacy_root:
        raise ValueError(f"{label} chain_root_hash alias does not match chain_anchor_hash")
    resolved = canonical_genesis or legacy_anchor or legacy_root
    if not resolved:
        raise ValueError(f"{label} missing genesis_hash")
    return resolved


def _resolve_payload_genesis_hash(*, payload: dict[str, object], label: str) -> str:
    return _resolve_export_genesis_hash(export=payload, label=label)


def _publication_bundle_published_at(*, export: dict[str, object], label: str) -> str:
    bundle = export.get("publication_bundle")
    if bundle is None:
        return ""
    if not isinstance(bundle, dict):
        raise ValueError(f"{label} publication_bundle must be an object")
    published_at = str(bundle.get("published_at") or "").strip()
    if not published_at:
        raise ValueError(f"{label} publication_bundle missing published_at")
    return published_at


def _require_supported_manifest_version(*, value: object, label: str) -> int:
    if value in (None, ""):
        raise ValueError(f"{label} missing config_manifest_version")
    manifest_version = _require_int(value, label=f"{label} config_manifest_version")
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
    if isinstance(value, bool) or not isinstance(value, int):
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


def _find_receipt_row(*, ordered: list[dict[str, object]], ballot_receipt_code: str) -> dict[str, object] | None:
    if not str(ballot_receipt_code or "").strip():
        return None

    for row in ordered:
        if str(row.get("ballot_hash") or "") == ballot_receipt_code:
            return row
    return None


def _raise_election_id_mismatch() -> None:
    raise ValueError(
        "election_id mismatch: exports do not match the requested election_id; use the "
        "public-ballots.json/public-audit.json pair for this election."
    )


def _raise_missing_public_audit_file_for_v2() -> None:
    raise ValueError(
        "public-audit.json missing for v2 verification; download the matching public-audit.json "
        "from the same publication bundle and rerun."
    )


def _raise_chain_version_mismatch() -> None:
    raise ValueError(
        "chain_version mismatch between public-ballots.json and public-audit.json; use the "
        "matching public-ballots/public-audit export pair for this election."
    )


def _raise_missing_v2_audit_log() -> None:
    raise ValueError(
        "public-audit.json must include audit_log for v2 verification; use the matching "
        "public-audit.json exported with this public-ballots.json."
    )


def _raise_v2_chain_root_kind_mismatch() -> None:
    raise ValueError(
        "v2 exports must use chain_root_kind=config_anchor_v2; use the matching "
        "public-ballots/public-audit export pair for this election."
    )


def _raise_publication_bundle_published_at_mismatch() -> None:
    raise ValueError(
        "publication_bundle.published_at mismatch between public-ballots.json and "
        "public-audit.json; use files from the same published bundle."
    )


def _raise_chain_head_mismatch() -> None:
    raise ValueError(
        "chain head mismatch: reconstructed chain does not match exported chain_head; "
        "re-download the published export(s) and rerun verification."
    )


def _raise_chain_anchor_mismatch() -> None:
    raise ValueError(
        "genesis_hash mismatch: computed genesis hash does not match exported genesis_hash; "
        "use the matching public-audit.json and config manifest for this election."
    )


def _raise_missing_v2_election_started_event() -> None:
    raise ValueError(
        "public-audit.json missing election_started event for v2 verification; use the matching "
        "public-audit.json exported with this public-ballots.json."
    )

def _raise_current_ledger_hash_mismatch() -> None:
    raise ValueError(
        "Entered current ledger hash does not match public-ballots.json chain_head. "
        "You likely pasted a receipt-level current ledger hash; use the election page hash or "
        "the export chain_head value."
    )


def _raise_receipt_previous_ledger_hash_mismatch() -> None:
    raise ValueError(
        "Entered receipt previous ledger hash does not match the located ballot row previous_chain_hash. "
        "Check the vote receipt's previous ledger hash for this ballot and rerun."
    )


def _raise_receipt_current_ledger_hash_mismatch() -> None:
    raise ValueError(
        "Entered receipt current ledger hash does not match the located ballot row chain_hash. "
        "Use the receipt-time current ledger hash for your ballot, not the final election chain head."
    )


def _rekor_guidance(*, verify_rekor_online: bool) -> str:
    if verify_rekor_online:
        return (
            "verify_rekor_online=True requests the audit/Rekor step, but verify-ballot-chain.py "
            "only verifies ballot-chain integrity. Run verify-audit-log.py with the same "
            "public-audit.json and verify_online=True to perform the online Rekor check."
        )
    return (
        "Local-only success: run verify-audit-log.py with the matching public-audit.json if you "
        "want a separate audit/Rekor verification step. That audit-only step does not by itself "
        "prove ballot-ledger integrity."
    )


def _verify_receipt_hash_inputs(
    *,
    your_row: dict[str, object] | None,
    receipt_previous_ledger_hash: str,
    receipt_current_ledger_hash: str,
) -> None:
    if your_row is None:
        return

    exported_previous = _clean_optional_text(your_row.get("previous_chain_hash"))
    if receipt_previous_ledger_hash and receipt_previous_ledger_hash != exported_previous:
        _raise_receipt_previous_ledger_hash_mismatch()

    exported_current = _clean_optional_text(your_row.get("chain_hash"))
    if receipt_current_ledger_hash and receipt_current_ledger_hash != exported_current:
        _raise_receipt_current_ledger_hash_mismatch()


def verify_export_bundle(
    *,
    ballots_export: dict[str, object],
    audit_export: dict[str, object],
    election_id: int,
    ballot_receipt_code: str,
    previous_ledger_hash: str,
    current_ledger_hash: str,
    receipt_previous_ledger_hash: str = "",
    receipt_current_ledger_hash: str = "",
    final_election_chain_head: str = "",
    verify_rekor_online: bool,
) -> dict[str, object]:
    resolved_inputs = _resolve_user_hash_inputs(
        previous_ledger_hash=previous_ledger_hash,
        current_ledger_hash=current_ledger_hash,
        receipt_previous_ledger_hash=receipt_previous_ledger_hash,
        receipt_current_ledger_hash=receipt_current_ledger_hash,
        final_election_chain_head=final_election_chain_head,
    )

    chain_version = _require_int(ballots_export.get("chain_version") or 1, label="public-ballots.json chain_version")
    audit_chain_version = _require_int(audit_export.get("chain_version") or 1, label="public-audit.json chain_version")
    if chain_version != audit_chain_version:
        _raise_chain_version_mismatch()

    export_election_id = _require_matching_metadata(
        ballots_export=ballots_export,
        audit_export=audit_export,
        key="election_id",
    )
    if int(export_election_id or 0) != election_id:
        _raise_election_id_mismatch()

    if chain_version == 1:
        genesis = compute_genesis_hash(election_id)
        ballots = ballots_export.get("ballots")
        if not isinstance(ballots, list):
            raise ValueError("public-ballots.json ballots must be a list")
        ordered = reconstruct_chain_order(ballots=ballots, genesis_hash=genesis)
        computed_head = ordered[-1]["chain_hash"] if ordered else genesis
        expected_head = str(ballots_export.get("chain_head") or "").strip()
        if (
            resolved_inputs["final_election_chain_head"]
            and resolved_inputs["final_election_chain_head"] != expected_head
        ):
            _raise_current_ledger_hash_mismatch()
        if computed_head != expected_head:
            raise ValueError("chain head mismatch")
        return {"status": "valid", "chain_head": computed_head}

    audit_log_obj = audit_export.get("audit_log")
    if not isinstance(audit_log_obj, list):
        _raise_missing_v2_audit_log()
    audit_log = cast(list[object], audit_log_obj)

    chain_root_kind = _require_matching_metadata(
        ballots_export=ballots_export,
        audit_export=audit_export,
        key="chain_root_kind",
    )
    if chain_root_kind != "config_anchor_v2":
        _raise_v2_chain_root_kind_mismatch()

    expected_head = _require_matching_metadata(
        ballots_export=ballots_export,
        audit_export=audit_export,
        key="chain_head",
    )
    ballots_genesis_hash = _resolve_export_genesis_hash(export=ballots_export, label="public-ballots.json")
    audit_genesis_hash = _resolve_export_genesis_hash(export=audit_export, label="public-audit.json")
    if ballots_genesis_hash != audit_genesis_hash:
        raise ValueError("genesis_hash mismatch between public-ballots.json and public-audit.json")
    genesis_hash = ballots_genesis_hash
    exported_manifest_version = _require_supported_manifest_version(
        value=_require_matching_metadata(
            ballots_export=ballots_export,
            audit_export=audit_export,
            key="config_manifest_version",
        ),
        label="public exports",
    )
    manifest_digest = _require_matching_metadata(
        ballots_export=ballots_export,
        audit_export=audit_export,
        key="config_manifest_sha256",
    )
    ballots_published_at = _publication_bundle_published_at(
        export=ballots_export,
        label="public-ballots.json",
    )
    audit_published_at = _publication_bundle_published_at(
        export=audit_export,
        label="public-audit.json",
    )
    publication_bundle_published_at = ""
    if ballots_published_at or audit_published_at:
        if not ballots_published_at or not audit_published_at or ballots_published_at != audit_published_at:
            _raise_publication_bundle_published_at_mismatch()
        publication_bundle_published_at = ballots_published_at

    election_started: dict[str, object] | None = None
    for event in audit_log:
        if isinstance(event, dict) and str(event.get("event_type") or "") == "election_started":
            election_started = event
            break
    if election_started is None:
        _raise_missing_v2_election_started_event()
    resolved_election_started = cast(dict[str, object], election_started)

    payload = resolved_election_started.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("election_started payload must be an object")
    manifest = payload.get("config_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("election_started payload missing config_manifest")
    payload_manifest_version = _require_supported_manifest_version(
        value=payload.get("config_manifest_version"),
        label="election_started payload",
    )
    manifest_version = _require_supported_manifest_version(
        value=manifest.get("version"),
        label="config_manifest",
    )
    if payload_manifest_version != exported_manifest_version:
        raise ValueError("config_manifest_version mismatch between public exports and election_started payload")
    if manifest_version != exported_manifest_version:
        raise ValueError("config_manifest_version mismatch between public exports and config_manifest")
    _validate_manifest_v1_schema(manifest=manifest)

    computed_manifest_digest = hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
    if computed_manifest_digest != manifest_digest:
        raise ValueError("config_manifest_sha256 mismatch")
    payload_manifest_digest = str(payload.get("config_manifest_sha256") or "").strip().lower()
    if payload_manifest_digest != computed_manifest_digest:
        raise ValueError("election_started payload config_manifest_sha256 mismatch")

    computed_anchor = compute_chain_anchor_hash(
        election_id=election_id,
        config_manifest_sha256=computed_manifest_digest,
    )
    payload_genesis_hash = _resolve_payload_genesis_hash(
        payload=payload,
        label="election_started payload",
    )
    if payload_genesis_hash != computed_anchor:
        raise ValueError("election_started payload genesis_hash mismatch")
    if genesis_hash != computed_anchor:
        _raise_chain_anchor_mismatch()

    ballots = ballots_export.get("ballots")
    if not isinstance(ballots, list):
        raise ValueError("public-ballots.json ballots must be a list")
    ordered = reconstruct_chain_order(
        ballots=ballots,
        genesis_hash=genesis_hash,
        origin_label="genesis hash",
    )
    computed_head = ordered[-1]["chain_hash"] if ordered else genesis_hash
    if (
        resolved_inputs["final_election_chain_head"]
        and resolved_inputs["final_election_chain_head"] != expected_head
    ):
        _raise_current_ledger_hash_mismatch()
    if computed_head != expected_head:
        _raise_chain_head_mismatch()

    your_row = _find_receipt_row(ordered=ordered, ballot_receipt_code=ballot_receipt_code)
    _verify_receipt_hash_inputs(
        your_row=your_row,
        receipt_previous_ledger_hash=resolved_inputs["receipt_previous_ledger_hash"],
        receipt_current_ledger_hash=resolved_inputs["receipt_current_ledger_hash"],
    )

    return {
        "status": "untrusted_local_only",
        "chain_head": computed_head,
        "chain_root_kind": chain_root_kind,
        "config_manifest_sha256": computed_manifest_digest,
        "genesis_hash": computed_anchor,
        "publication_bundle_published_at": publication_bundle_published_at,
        "receipt_found": your_row is not None,
        "receipt_previous_chain_hash": "" if your_row is None else str(your_row.get("previous_chain_hash") or ""),
        "receipt_chain_hash": "" if your_row is None else str(your_row.get("chain_hash") or ""),
        "rekor_online_requested": verify_rekor_online,
        "rekor_guidance": _rekor_guidance(verify_rekor_online=verify_rekor_online),
    }


def verify_public_ballot_export(
    *,
    ballots_export: dict[str, object],
    audit_export: dict[str, object] | None,
    election_id: int,
    ballot_receipt_code: str,
    previous_ledger_hash: str,
    current_ledger_hash: str,
    receipt_previous_ledger_hash: str = "",
    receipt_current_ledger_hash: str = "",
    final_election_chain_head: str = "",
    verify_rekor_online: bool = False,
) -> dict[str, object]:
    chain_version = _require_int(ballots_export.get("chain_version") or 1, label="public-ballots.json chain_version")
    resolved_inputs = _resolve_user_hash_inputs(
        previous_ledger_hash=previous_ledger_hash,
        current_ledger_hash=current_ledger_hash,
        receipt_previous_ledger_hash=receipt_previous_ledger_hash,
        receipt_current_ledger_hash=receipt_current_ledger_hash,
        final_election_chain_head=final_election_chain_head,
    )

    if chain_version == 1:
        genesis = compute_genesis_hash(election_id)
        export_election_id = str(ballots_export.get("election_id") or "").strip()
        if export_election_id and int(export_election_id) != election_id:
            _raise_election_id_mismatch()

        root_kind = str(ballots_export.get("chain_root_kind") or "").strip()
        if root_kind and root_kind != "legacy_genesis":
            raise ValueError("chain_root_kind mismatch")

        exported_genesis = _clean_optional_text(ballots_export.get("genesis_hash")).lower()
        legacy_root_hash = _clean_optional_text(ballots_export.get("chain_root_hash")).lower()
        if exported_genesis and exported_genesis != genesis:
            raise ValueError("genesis_hash mismatch")
        if legacy_root_hash and legacy_root_hash != genesis:
            raise ValueError("chain_root_hash mismatch")
        if exported_genesis and legacy_root_hash and exported_genesis != legacy_root_hash:
            raise ValueError("chain_root_hash alias does not match genesis_hash")

        ballots = ballots_export.get("ballots")
        if not isinstance(ballots, list):
            raise ValueError("public-ballots.json ballots must be a list")
        ordered = reconstruct_chain_order(ballots=ballots, genesis_hash=genesis)
        expected_head = str(ballots_export.get("chain_head") or "").strip()
        computed_head = ordered[-1]["chain_hash"] if ordered else genesis
        if (
            resolved_inputs["final_election_chain_head"]
            and resolved_inputs["final_election_chain_head"] != expected_head
        ):
            _raise_current_ledger_hash_mismatch()
        if computed_head != expected_head:
            _raise_chain_head_mismatch()

        your_row = None
        for row in ordered:
            if str(row.get("ballot_hash") or "") == ballot_receipt_code:
                your_row = row
                break

        _verify_receipt_hash_inputs(
            your_row=your_row,
            receipt_previous_ledger_hash=resolved_inputs["receipt_previous_ledger_hash"],
            receipt_current_ledger_hash=resolved_inputs["receipt_current_ledger_hash"],
        )

        return {
            "status": "valid",
            "chain_version": 1,
            "chain_root_kind": "legacy_genesis",
            "genesis_hash": genesis,
            "chain_head": computed_head,
            "total_ballots": len(ordered),
            "receipt_found": your_row is not None,
            "receipt_previous_chain_hash": "" if your_row is None else str(your_row.get("previous_chain_hash") or ""),
            "receipt_chain_hash": "" if your_row is None else str(your_row.get("chain_hash") or ""),
            "rekor_online_requested": False,
            "rekor_guidance": "",
        }

    if audit_export is None:
        _raise_missing_public_audit_file_for_v2()
    resolved_audit_export = cast(dict[str, object], audit_export)

    result = verify_export_bundle(
        ballots_export=ballots_export,
        audit_export=resolved_audit_export,
        election_id=election_id,
        ballot_receipt_code=ballot_receipt_code,
        previous_ledger_hash=previous_ledger_hash,
        current_ledger_hash=current_ledger_hash,
        receipt_previous_ledger_hash=resolved_inputs["receipt_previous_ledger_hash"],
        receipt_current_ledger_hash=resolved_inputs["receipt_current_ledger_hash"],
        final_election_chain_head=resolved_inputs["final_election_chain_head"],
        verify_rekor_online=verify_rekor_online,
    )
    ballots = ballots_export.get("ballots")
    if not isinstance(ballots, list):
        raise ValueError("public-ballots.json ballots must be a list")
    result["chain_version"] = 2
    result["total_ballots"] = len(ballots)
    return result

if __name__ == "__main__":
    with open(ballots_file, encoding="utf-8") as f:
        ballots_export = json.load(f)

    if not isinstance(ballots_export, dict):
        raise SystemExit("public-ballots.json must contain a JSON object")

    export_election_id = ballots_export.get("election_id")
    if export_election_id is not None:
        try:
            export_election_id = int(export_election_id)
        except (TypeError, ValueError):
            raise SystemExit("public-ballots.json election_id must be an integer")

    if export_election_id is not None and export_election_id != election_id:
        raise SystemExit(
            f"election_id mismatch: script={election_id} export={export_election_id}. "
            "Copy the election_id from the election page or the verify page."
        )

    audit_export: dict[str, object] | None = None
    if int(ballots_export.get("chain_version") or 1) == 2:
        with open(audit_file, encoding="utf-8") as f:
            loaded_audit = json.load(f)
        if not isinstance(loaded_audit, dict):
            raise SystemExit("public-audit.json must contain a JSON object")
        audit_export = loaded_audit

    try:
        result = verify_public_ballot_export(
            ballots_export=ballots_export,
            audit_export=audit_export,
            election_id=election_id,
            ballot_receipt_code=ballot_receipt_code,
            previous_ledger_hash="",
            current_ledger_hash="",
            receipt_previous_ledger_hash=receipt_previous_ledger_hash,
            receipt_current_ledger_hash=receipt_current_ledger_hash,
            final_election_chain_head=final_election_chain_head,
            verify_rekor_online=verify_rekor_online,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("Ballot Chain Verification")
    print("=" * 60)
    print(f"Election ID:        {election_id}")
    print(f"Chain version:      {result['chain_version']}")
    print(f"Total ballots:      {result['total_ballots']}")
    print(f"Ballot receipt code: {ballot_receipt_code}")
    if result["chain_version"] == 1:
        print(f"Chain root kind:    {result['chain_root_kind']}")
        print(f"Genesis hash:       {result['genesis_hash']}")
    else:
        print(f"Genesis hash:       {result['genesis_hash']}")
        if str(result.get("config_manifest_sha256") or ""):
            print(f"Election definition digest: {result['config_manifest_sha256']}")
        print("For v2 elections, genesis_hash is the manifest-derived chain anchor.")
    if str(result.get("publication_bundle_published_at") or ""):
        print(f"Publication pair:   {result['publication_bundle_published_at']}")
    print(f"Verified path:      {result['genesis_hash']} -> {result['chain_head']}")
    print()

    if result["receipt_found"]:
        print("→ Your ballot receipt code appears in the public ballots export.")
        exported_previous = str(result.get("receipt_previous_chain_hash") or "")
        print(f"  Exported receipt previous ledger hash: {exported_previous}")
        if receipt_previous_ledger_hash:
            print("  ✓ Receipt previous ledger hash matches your receipt.")
        exported_receipt_current = str(result.get("receipt_chain_hash") or "")
        print(f"  Exported receipt current ledger hash:  {exported_receipt_current}")
        if receipt_current_ledger_hash:
            print("  ✓ Receipt current ledger hash matches your receipt.")
        print()
    else:
        print("✗ Your ballot receipt code was not found in the export.")
        print()

    if result["chain_version"] == 1:
        print("✓ Chain integrity verified: genesis hash -> chain head is a single, complete path")
    else:
        print("✓ Chain integrity verified: genesis hash -> chain head is a single, complete path")
    print(f"  Final election chain head: {result['chain_head']}")
    if final_election_chain_head:
        print("  ✓ Final election chain head matches public-ballots.json chain_head.")

    if result["status"] == "untrusted_local_only":
        print(f"! {result['rekor_guidance']}")

    if not result["receipt_found"]:
        raise SystemExit(3)
    raise SystemExit(0 if result["status"] == "valid" else 2)
