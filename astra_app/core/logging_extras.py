import sys


def exception_log_fields(error: BaseException) -> dict[str, str]:
    return {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "error_repr": repr(error),
        "error_args": repr(error.args),
    }


def current_exception_log_fields() -> dict[str, str]:
    """Return structured error fields for the current exception.

    Intended for use with ``logger.exception(..., extra=...)`` so logs always
    include normalized exception details even when the caller doesn't have a
    named exception variable.
    """

    exc = sys.exception()
    if exc is None:
        return {}
    return exception_log_fields(exc)
