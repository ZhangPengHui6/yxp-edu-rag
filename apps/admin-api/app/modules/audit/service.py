"""Audit + outbox writes; every mutating use case calls ``record``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_client_ip, current_request_id
from app.models import AuditLog, OutboxEvent
from app.modules.audit.schemas import AuditLogOut
from app.shared.time import fmt_dt


def record(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    action: str,
    resource_type: str,
    resource_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name=actor_name,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before or {},
            after=after or {},
            ip=current_client_ip(),
            request_id=current_request_id(),
        )
    )


def emit(
    session: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload or {},
        )
    )


async def list_logs(
    session: AsyncSession,
    *,
    tenant_id: str | None,
    page: int,
    page_size: int,
    actor: str | None = None,
    action: str | None = None,
) -> tuple[list[AuditLogOut], int]:
    filters = []
    if tenant_id:
        filters.append(AuditLog.tenant_id == tenant_id)
    if actor:
        filters.append(AuditLog.actor_name.contains(actor))
    if action:
        filters.append(AuditLog.action.startswith(action))
    stmt = select(AuditLog).where(*filters)
    count_stmt = select(func.count()).select_from(AuditLog).where(*filters)
    total = int((await session.execute(count_stmt)).scalar_one())
    logs = (
        (
            await session.execute(
                stmt.order_by(AuditLog.log_id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        AuditLogOut(
            id=str(log.log_id),
            actor=log.actor_name,
            action=log.action,
            target=f"{log.resource_type}:{log.resource_id}" if log.resource_type else log.resource_id,
            ip=log.ip,
            created_at=fmt_dt(log.created_at),
        )
        for log in logs
    ]
    return items, total
