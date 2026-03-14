def exception_log_fields(error: BaseException) -> dict[str, str]:
    return {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "error_repr": repr(error),
        "error_args": repr(error.args),
    }
