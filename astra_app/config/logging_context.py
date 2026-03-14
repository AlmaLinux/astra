from contextvars import ContextVar, Token
from typing import TypedDict


class RequestLogContext(TypedDict):
    client_ip: str | None
    user_id: str | None
    request_id: str | None
    request_path: str
    request_method: str


_REQUEST_LOG_CONTEXT: ContextVar[RequestLogContext | None] = ContextVar(
    "astra_request_log_context",
    default=None,
)


def set_request_log_context(context: RequestLogContext) -> Token[RequestLogContext | None]:
    return _REQUEST_LOG_CONTEXT.set(context)


def reset_request_log_context(token: Token[RequestLogContext | None]) -> None:
    _REQUEST_LOG_CONTEXT.reset(token)


def get_request_log_context() -> RequestLogContext | None:
    return _REQUEST_LOG_CONTEXT.get()
