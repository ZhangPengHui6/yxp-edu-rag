"""API-key authentication for ``/v1``.

The PRD authenticates student calls with an API key. The admin schema keeps
``api_keys`` for per-tenant keys, but there is no issuance flow yet, so this
minimal service compares against ``KB_API_KEY`` from the environment. When the
key is empty the service runs in local-development mode and logs a warning.
"""

from __future__ import annotations

import hmac

from app.core.errors import Unauthorized
from app.core.logging import get_logger
from fastapi import Header

from kb_app.config import get_kb_settings

log = get_logger("kb.auth")


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    settings = get_kb_settings()
    expected = settings.kb_api_key.strip()
    if not expected:
        return "dev"
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise Unauthorized("API Key 无效")
    return x_api_key
