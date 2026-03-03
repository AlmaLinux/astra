import hashlib
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core import signing

TOKEN_SALT_PREFIX = "astra.core.tokens:v1:"
_PASSWORD_RESET_TOKEN_PURPOSE = "password-reset"
_REGISTRATION_ACTIVATION_TOKEN_PURPOSE = "registration-activate"
_SETTINGS_EMAIL_VALIDATION_TOKEN_PURPOSE = "settings-email-validate"
_ORGANIZATION_CLAIM_TOKEN_PURPOSE = "org_claim"
_ACCOUNT_INVITATION_TOKEN_PURPOSE = "account-invitation"

ORGANIZATION_CLAIM_TOKEN_TTL_SECONDS = int(timedelta(days=7).total_seconds())


def _salt_for_purpose(*, purpose: str) -> str:
    return f"{TOKEN_SALT_PREFIX}{purpose}"


def _dumps(*, purpose: str, payload: Mapping[str, Any]) -> str:
    return signing.dumps(dict(payload), salt=_salt_for_purpose(purpose=purpose))


def _loads(*, purpose: str, token: str, max_age_seconds: int | None) -> dict[str, Any]:
    payload = signing.loads(
        token,
        salt=_salt_for_purpose(purpose=purpose),
        max_age=max_age_seconds,
    )
    if not isinstance(payload, dict):
        raise signing.BadSignature("Invalid token payload")
    return payload


def make_password_reset_token(payload: Mapping[str, Any]) -> str:
    return _dumps(purpose=_PASSWORD_RESET_TOKEN_PURPOSE, payload=payload)


def read_password_reset_token(token: str) -> dict[str, Any]:
    return _loads(
        purpose=_PASSWORD_RESET_TOKEN_PURPOSE,
        token=token,
        max_age_seconds=settings.PASSWORD_RESET_TOKEN_TTL_SECONDS,
    )


def make_registration_activation_token(payload: Mapping[str, Any]) -> str:
    return _dumps(purpose=_REGISTRATION_ACTIVATION_TOKEN_PURPOSE, payload=payload)


def read_registration_activation_token(token: str) -> dict[str, Any]:
    return _loads(
        purpose=_REGISTRATION_ACTIVATION_TOKEN_PURPOSE,
        token=token,
        max_age_seconds=settings.EMAIL_VALIDATION_TOKEN_TTL_SECONDS,
    )


def make_settings_email_validation_token(payload: Mapping[str, Any]) -> str:
    return _dumps(purpose=_SETTINGS_EMAIL_VALIDATION_TOKEN_PURPOSE, payload=payload)


def read_settings_email_validation_token(token: str) -> dict[str, Any]:
    return _loads(
        purpose=_SETTINGS_EMAIL_VALIDATION_TOKEN_PURPOSE,
        token=token,
        max_age_seconds=settings.EMAIL_VALIDATION_TOKEN_TTL_SECONDS,
    )


def make_organization_claim_token(payload: Mapping[str, Any]) -> str:
    return _dumps(purpose=_ORGANIZATION_CLAIM_TOKEN_PURPOSE, payload=payload)


def read_organization_claim_token(token: str) -> dict[str, Any]:
    return _loads(
        purpose=_ORGANIZATION_CLAIM_TOKEN_PURPOSE,
        token=token,
        max_age_seconds=ORGANIZATION_CLAIM_TOKEN_TTL_SECONDS,
    )


def make_account_invitation_token(payload: Mapping[str, Any]) -> str:
    return _dumps(purpose=_ACCOUNT_INVITATION_TOKEN_PURPOSE, payload=payload)


def read_account_invitation_token_unbounded(token: str) -> dict[str, Any]:
    return _loads(
        purpose=_ACCOUNT_INVITATION_TOKEN_PURPOSE,
        token=token,
        max_age_seconds=None,
    )


def _make_signed_token_legacy(payload: Mapping[str, Any]) -> str:
    return signing.dumps(dict(payload), salt=settings.SECRET_KEY)


def _read_signed_token_unbounded_legacy(token: str) -> dict[str, Any]:
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
