import datetime
import json
from hashlib import sha256

from core.elections_meek import MEEK_DEFAULT_EPSILON, MEEK_DEFAULT_MAX_ITERATIONS
from core.models import Candidate, Election, ExclusionGroup, ExclusionGroupCandidate
from core.tokens import election_chain_anchor_hash, election_genesis_chain_hash

CHAIN_VERSION_LEGACY = 1
CHAIN_VERSION_CONFIG_ANCHOR_V2 = 2
CONFIG_MANIFEST_VERSION = 1
CHAIN_ROOT_KIND_LEGACY = "legacy_genesis"
CHAIN_ROOT_KIND_CONFIG_ANCHOR_V2 = "config_anchor_v2"
TALLY_RULE_ALGORITHM = "Meek STV (High-Precision Variant)"
TALLY_RULE_ALGORITHM_VERSION = "1.0"
TALLY_RULE_SPEC_IDENTITY = "docs/runbooks/meek-stv-elections.md"


def _canonical_datetime_utc(value: datetime.datetime) -> str:
    normalized = value.astimezone(datetime.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def load_locked_manifest_source_rows(
    *,
    election: Election,
) -> tuple[list[Candidate], list[ExclusionGroup], list[dict[str, int]]]:
    candidate_rows = list(
        Candidate.objects.select_for_update()
        .filter(election=election)
        .only("id", "freeipa_username", "nominated_by", "tiebreak_uuid")
        .order_by("id")
    )
    groups = list(
        ExclusionGroup.objects.select_for_update()
        .filter(election=election)
        .only("id", "public_id", "name", "max_elected")
        .order_by("public_id")
    )
    group_candidates = list(
        ExclusionGroupCandidate.objects.select_for_update()
        .filter(exclusion_group__election=election)
        .values("exclusion_group_id", "candidate_id")
        .order_by("candidate_id")
    )
    return candidate_rows, groups, group_candidates


def build_config_manifest(
    *,
    election: Election,
    candidate_rows: list[Candidate] | None = None,
    groups: list[ExclusionGroup] | None = None,
    group_candidates: list[dict[str, int]] | None = None,
) -> dict[str, object]:
    if candidate_rows is None:
        candidate_rows = list(
            Candidate.objects.filter(election=election)
            .only("id", "freeipa_username", "nominated_by", "tiebreak_uuid")
            .order_by("id")
        )
    if groups is None:
        groups = list(
            ExclusionGroup.objects.filter(election=election)
            .only("id", "public_id", "name", "max_elected")
            .order_by("public_id")
        )
    if group_candidates is None:
        group_candidates = list(
            ExclusionGroupCandidate.objects.filter(exclusion_group__election=election)
            .values("exclusion_group_id", "candidate_id")
            .order_by("candidate_id")
        )

    candidate_ids_by_group_id: dict[int, list[int]] = {}
    for row in group_candidates:
        group_id = int(row["exclusion_group_id"])
        candidate_ids_by_group_id.setdefault(group_id, []).append(int(row["candidate_id"]))

    return {
        "version": CONFIG_MANIFEST_VERSION,
        "election": {
            "id": int(election.id),
            "name": str(election.name),
            "start_datetime": _canonical_datetime_utc(election.start_datetime),
            "number_of_seats": int(election.number_of_seats),
            "quorum": int(election.quorum),
            "eligible_group_cn": str(election.eligible_group_cn or ""),
        },
        "tally_rule": {
            "algorithm": TALLY_RULE_ALGORITHM,
            "algorithm_version": TALLY_RULE_ALGORITHM_VERSION,
            "spec_identity": TALLY_RULE_SPEC_IDENTITY,
            "epsilon": str(MEEK_DEFAULT_EPSILON),
            "max_iterations": int(MEEK_DEFAULT_MAX_ITERATIONS),
        },
        "candidates": [
            {
                "id": int(candidate.id),
                "freeipa_username": str(candidate.freeipa_username),
                "nominated_by": str(candidate.nominated_by),
                "tiebreak_uuid": str(candidate.tiebreak_uuid),
            }
            for candidate in candidate_rows
        ],
        "exclusion_groups": [
            {
                "public_id": str(group.public_id),
                "name": str(group.name),
                "max_elected": int(group.max_elected),
                "candidate_ids": sorted(candidate_ids_by_group_id.get(int(group.id), [])),
            }
            for group in groups
        ],
    }


def canonical_config_manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def config_manifest_sha256(manifest: dict[str, object]) -> str:
    return sha256(canonical_config_manifest_bytes(manifest)).hexdigest()


def resolve_public_genesis_hash(*, payload: dict[str, object]) -> str:
    resolved_hash = ""
    for field_name in ("genesis_hash", "chain_anchor_hash", "genesis_chain_hash", "chain_root_hash"):
        field_value = str(payload.get(field_name) or "").strip()
        if not field_value:
            continue
        if resolved_hash and field_value != resolved_hash:
            raise ValueError(f"{field_name} alias does not match genesis_hash")
        resolved_hash = field_value
    return resolved_hash


def stored_config_manifest(*, election: Election) -> dict[str, object]:
    manifest = election.config_manifest
    if not isinstance(manifest, dict):
        raise ValueError("Election config_manifest must be a JSON object for v2 elections")
    return manifest


def manifest_candidate_username_by_id_map(*, manifest: dict[str, object]) -> dict[int, str]:
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("config_manifest candidates must be a list")

    by_id: dict[int, str] = {}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"config_manifest candidates[{index}] must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, int):
            raise ValueError(f"config_manifest candidates[{index}].id must be an integer")
        if candidate_id in by_id:
            raise ValueError(f"config_manifest candidates[{index}].id is duplicated: {candidate_id}")

        for field_name in ("freeipa_username", "nominated_by", "tiebreak_uuid"):
            field_value = candidate.get(field_name)
            if not isinstance(field_value, str):
                raise ValueError(f"config_manifest candidates[{index}].{field_name} must be a string")

        username = str(candidate["freeipa_username"]).strip()
        if not username:
            raise ValueError(f"config_manifest candidates[{index}].freeipa_username must not be blank")

        by_id[candidate_id] = str(candidate["freeipa_username"])
    return by_id


def validated_v2_manifest_state(*, election: Election) -> dict[str, object]:
    if int(election.chain_version or CHAIN_VERSION_LEGACY) != CHAIN_VERSION_CONFIG_ANCHOR_V2:
        raise ValueError("validated_v2_manifest_state requires chain_version=2")

    manifest_version = int(election.config_manifest_version or 0)
    if manifest_version != CONFIG_MANIFEST_VERSION:
        raise ValueError("config_manifest_version mismatch")

    manifest = stored_config_manifest(election=election)
    if int(manifest.get("version") or 0) != CONFIG_MANIFEST_VERSION:
        raise ValueError("config_manifest version mismatch")

    election_payload = manifest.get("election")
    if not isinstance(election_payload, dict):
        raise ValueError("config_manifest election must be an object")

    try:
        manifest_election_id = int(election_payload.get("id"))
    except (TypeError, ValueError):
        raise ValueError("config_manifest election.id must be an integer") from None
    if manifest_election_id != int(election.id):
        raise ValueError("config_manifest election.id mismatch")

    for field_name in Election.v2_manifest_election_field_names():
        if field_name in {"id", "number_of_seats", "quorum"}:
            continue
        if not isinstance(election_payload.get(field_name), str):
            raise ValueError(f"config_manifest election.{field_name} must be a string")
    for field_name in ("number_of_seats", "quorum"):
        if not isinstance(election_payload.get(field_name), int):
            raise ValueError(f"config_manifest election.{field_name} must be an integer")

    tally_rule = manifest.get("tally_rule")
    if not isinstance(tally_rule, dict):
        raise ValueError("config_manifest tally_rule must be an object")
    for field_name in ("algorithm", "algorithm_version", "spec_identity", "epsilon"):
        if not isinstance(tally_rule.get(field_name), str):
            raise ValueError(f"config_manifest tally_rule.{field_name} must be a string")
    if not isinstance(tally_rule.get("max_iterations"), int):
        raise ValueError("config_manifest tally_rule.max_iterations must be an integer")

    candidate_username_by_id = manifest_candidate_username_by_id_map(manifest=manifest)

    exclusion_groups = manifest.get("exclusion_groups")
    if not isinstance(exclusion_groups, list):
        raise ValueError("config_manifest exclusion_groups must be a list")
    for index, group in enumerate(exclusion_groups):
        if not isinstance(group, dict):
            raise ValueError(f"config_manifest exclusion_groups[{index}] must be an object")
        for field_name in ("public_id", "name"):
            if not isinstance(group.get(field_name), str):
                raise ValueError(f"config_manifest exclusion_groups[{index}].{field_name} must be a string")
        if not isinstance(group.get("max_elected"), int):
            raise ValueError(f"config_manifest exclusion_groups[{index}].max_elected must be an integer")
        candidate_ids = group.get("candidate_ids")
        if not isinstance(candidate_ids, list) or any(not isinstance(candidate_id, int) for candidate_id in candidate_ids):
            raise ValueError(f"config_manifest exclusion_groups[{index}].candidate_ids must be a list of integers")

    computed_digest = config_manifest_sha256(manifest)
    stored_digest = str(election.config_manifest_sha256 or "").strip().lower()
    if computed_digest != stored_digest:
        raise ValueError("config_manifest_sha256 mismatch")

    computed_anchor = election_chain_anchor_hash(
        election_id=election.id,
        config_manifest_sha256=computed_digest,
    )
    stored_anchor = str(election.chain_anchor_hash or "").strip().lower()
    if computed_anchor != stored_anchor:
        raise ValueError("chain_anchor_hash mismatch")

    return {
        "config_manifest_version": manifest_version,
        "manifest": manifest,
        "config_manifest_sha256": computed_digest,
        "genesis_hash": computed_anchor,
        "candidate_username_by_id": candidate_username_by_id,
    }


def election_genesis_hash(*, election: Election) -> str:
    if int(election.chain_version or CHAIN_VERSION_LEGACY) == CHAIN_VERSION_CONFIG_ANCHOR_V2:
        genesis_hash = str(election.chain_anchor_hash or "").strip()
        if not genesis_hash:
            raise ValueError("v2 election is missing chain_anchor_hash")
        return genesis_hash
    return election_genesis_chain_hash(election.id)


def election_chain_root_kind(*, election: Election) -> str:
    if int(election.chain_version or CHAIN_VERSION_LEGACY) == CHAIN_VERSION_CONFIG_ANCHOR_V2:
        return CHAIN_ROOT_KIND_CONFIG_ANCHOR_V2
    return CHAIN_ROOT_KIND_LEGACY


def election_root_metadata(
    *,
    election: Election,
    chain_head: str | None = None,
    v2_manifest_state: dict[str, object] | None = None,
    published_at: datetime.datetime | None = None,
) -> dict[str, object]:
    genesis_hash = election_genesis_hash(election=election)
    metadata: dict[str, object] = {
        "election_id": int(election.id),
        "chain_version": int(election.chain_version or CHAIN_VERSION_LEGACY),
        "chain_root_kind": election_chain_root_kind(election=election),
        "genesis_hash": genesis_hash,
        "chain_head": str(chain_head or genesis_hash),
    }
    if int(election.chain_version or CHAIN_VERSION_LEGACY) == CHAIN_VERSION_CONFIG_ANCHOR_V2:
        if v2_manifest_state is None:
            metadata["config_manifest_version"] = int(election.config_manifest_version or CONFIG_MANIFEST_VERSION)
            metadata["config_manifest_sha256"] = str(election.config_manifest_sha256 or "")
        else:
            metadata["config_manifest_version"] = int(v2_manifest_state["config_manifest_version"])
            metadata["config_manifest_sha256"] = str(v2_manifest_state["config_manifest_sha256"])
    if published_at is not None:
        metadata["publication_bundle"] = {
            "published_at": _canonical_datetime_utc(published_at),
        }
    return metadata