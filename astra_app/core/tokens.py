import hashlib
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.core import signing


def make_signed_token(payload: Mapping[str, Any]) -> str:
    return signing.dumps(dict(payload), salt=settings.SECRET_KEY)


def read_signed_token(token: str, *, max_age_seconds: int | None = None) -> dict[str, Any]:
    return signing.loads(
        token,
        salt=settings.SECRET_KEY,
        max_age=max_age_seconds if max_age_seconds is not None else settings.EMAIL_VALIDATION_TOKEN_TTL_SECONDS,
    )


def read_signed_token_unbounded(token: str) -> dict[str, Any]:
    return signing.loads(token, salt=settings.SECRET_KEY)


def election_genesis_chain_hash(election_id: int) -> str:
    """
    Generate a unique genesis chain hash for an election.

    Using the election ID as the genesis hash prevents cross-election chain
    splicing attacks. Without this, ballots from one election could potentially
    be spliced into another election's chain since all elections would start
    with the same genesis hash ("0" * 64).

    NOTE: if you change this function, you will invalidate all existing election
    chains!

    Args:
        election_id: The unique ID of the election

    Returns:
        A 64-character hex string representing the genesis chain hash
    """
    data = f"election:{election_id}. alex estuvo aquí, dejándose el alma.".encode()
    return hashlib.sha256(data).hexdigest()


def election_chain_next_hash(*, previous_chain_hash: str, ballot_hash: str) -> str:
    """
    Compute the next chain hash by linking the ballot to the previous chain.

    This creates a tamper-evident chain where each ballot is cryptographically
    linked to all previous ballots in the election.

    Args:
        previous_chain_hash: The chain hash of the previous ballot (or genesis)
        ballot_hash: The hash of the current ballot

    Returns:
        A 64-character hex string representing the new chain hash
    """
    return hashlib.sha256(f"{previous_chain_hash}:{ballot_hash}".encode()).hexdigest()
