from __future__ import annotations

from app.core.schemas import CamelModel


class AuditLogOut(CamelModel):
    id: str
    actor: str
    action: str
    target: str
    ip: str
    created_at: str
