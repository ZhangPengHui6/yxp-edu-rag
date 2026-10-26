"""Response envelope shared by every admin endpoint."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.core.context import current_request_id

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    code: int = 0
    message: str = "ok"
    data: T | None = None
    request_id: str = "-"


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {
        "code": 0,
        "message": message,
        "data": data,
        "request_id": current_request_id(),
    }


def fail(
    *,
    code: int,
    message: str,
    error_code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = current_request_id()
    return {
        "code": code,
        "message": message,
        "data": None,
        "request_id": request_id,
        "error": {
            "code": error_code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        },
    }
