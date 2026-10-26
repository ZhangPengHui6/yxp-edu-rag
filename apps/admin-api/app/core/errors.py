"""Unified error model.

HTTP body is the flat admin envelope the existing frontend consumes, plus the
nested ``error`` object prescribed by 总纲 §6.5:

    {"code": 40401, "message": "...", "data": null, "request_id": "...",
     "error": {"code": "NOT_FOUND", "message": "...", "details": {}, "request_id": "..."}}
"""

from __future__ import annotations

from typing import Any

from fastapi import status


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int = 40001,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        error_code: str = "INVALID_ARGUMENT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.error_code = error_code
        self.details = details or {}


class NotFound(AppError):
    def __init__(self, message: str = "资源不存在", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=40401,
            http_status=status.HTTP_404_NOT_FOUND,
            error_code="NOT_FOUND",
            details=details,
        )


class Unauthorized(AppError):
    def __init__(self, message: str = "登录已失效，请重新登录") -> None:
        super().__init__(
            message,
            code=40101,
            http_status=status.HTTP_401_UNAUTHORIZED,
            error_code="UNAUTHORIZED",
        )


class Forbidden(AppError):
    def __init__(self, message: str = "没有操作权限", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=40301,
            http_status=status.HTTP_403_FORBIDDEN,
            error_code="FORBIDDEN",
            details=details,
        )


class Conflict(AppError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=40901,
            http_status=status.HTTP_409_CONFLICT,
            error_code="CONFLICT",
            details=details,
        )


class InvalidState(AppError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=40902,
            http_status=status.HTTP_409_CONFLICT,
            error_code="INVALID_STATE",
            details=details,
        )


class ProviderUnavailable(AppError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=50301,
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="PROVIDER_UNAVAILABLE",
            details=details,
        )
