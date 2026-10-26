"""Per-request context propagated to services, audit and logs."""

from __future__ import annotations

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
client_ip_ctx: ContextVar[str] = ContextVar("client_ip", default="-")
user_agent_ctx: ContextVar[str] = ContextVar("user_agent", default="-")


def current_request_id() -> str:
    return request_id_ctx.get()


def current_client_ip() -> str:
    return client_ip_ctx.get()
