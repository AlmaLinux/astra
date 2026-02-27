"""FreeIPA exception classes."""


class FreeIPAOperationFailed(RuntimeError):
    """Raised when FreeIPA returns a structured failure without raising."""


class FreeIPAUnavailableError(RuntimeError):
    """Raised when elections-critical FreeIPA lookups cannot be completed."""


class FreeIPAMisconfiguredError(RuntimeError):
    """Raised when elections-critical FreeIPA configuration is missing."""


__all__ = [
    "FreeIPAOperationFailed",
    "FreeIPAUnavailableError",
    "FreeIPAMisconfiguredError",
]
