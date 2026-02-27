"""FreeIPA model compatibility exports.

Re-export FreeIPA-facing classes and exceptions from the freeipa package.
"""

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.auth_backend import FreeIPAAuthBackend
from core.freeipa.exceptions import (
    FreeIPAMisconfiguredError,
    FreeIPAOperationFailed,
    FreeIPAUnavailableError,
)
from core.freeipa.group import FreeIPAGroup, get_freeipa_group_for_elections
from core.freeipa.user import DegradedFreeIPAUser, FreeIPAManager, FreeIPAUser
from core.freeipa.utils import _raise_if_freeipa_failed

__all__ = [
    "FreeIPAOperationFailed",
    "FreeIPAUnavailableError",
    "FreeIPAMisconfiguredError",
    "_raise_if_freeipa_failed",
    "DegradedFreeIPAUser",
    "FreeIPAManager",
    "FreeIPAUser",
    "FreeIPAGroup",
    "FreeIPAFASAgreement",
    "FreeIPAAuthBackend",
    "get_freeipa_group_for_elections",
]
