
from core.tokens import election_chain_next_hash


def compute_chain_hash(*, previous_chain_hash: str, ballot_hash: str) -> str:
    """
    Compute the next chain hash.
    
    Deprecated: Use election_chain_next_hash from core.tokens instead.
    This wrapper is kept for backward compatibility with existing tests.
    """
    return election_chain_next_hash(previous_chain_hash=previous_chain_hash, ballot_hash=ballot_hash)
