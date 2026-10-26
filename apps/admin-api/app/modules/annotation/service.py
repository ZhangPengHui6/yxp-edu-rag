"""Annotation queue use cases."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, NotFound
from app.models import AnnotationTask
from app.modules.annotation import repository as repo
from app.modules.annotation.schemas import (
    AnnotationBlockOut,
    AnnotationTaskOut,
    AnnotationTaskRowOut,
    ResolveIn,
    ResolveOut,
)
from app.modules.audit import service as audit
from app.shared.time import utcnow


async def list_tasks(
    session: AsyncSession, *, tenant_id: str, page: int, page_size: int
) -> tuple[list[AnnotationTaskRowOut], int]:
    filters = [AnnotationTask.tenant_id == tenant_id]
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(AnnotationTask).where(*filters)
            )
        ).scalar_one()
    )
    tasks = (
        (
            await session.execute(
                select(AnnotationTask)
                .where(*filters)
                .order_by(AnnotationTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items: list[AnnotationTaskRowOut] = []
    for task in tasks:
        _, pending = await repo.block_counts(task.task_id)
        items.append(
            AnnotationTaskRowOut(
                id=task.task_id,
                book=task.book_title,
                chapter=task.chapter_title,
                page=task.page,
                pending=pending,
                total=task.total,
            )
        )
    return items, total


async def get_task(
    session: AsyncSession, *, tenant_id: str, task_id: str
) -> AnnotationTaskOut:
    task = await session.get(AnnotationTask, task_id)
    if task is None or task.tenant_id != tenant_id:
        raise NotFound("校对任务不存在")
    blocks = await repo.blocks_for(task_id)
    return AnnotationTaskOut(
        id=task.task_id,
        book=task.book_title,
        chapter=task.chapter_title,
        page=task.page,
        page_total=task.page_total,
        cursor=task.cursor,
        total=task.total,
        blocks=[
            AnnotationBlockOut(
                id=block["_id"],
                index=int(block.get("block_index", 0)),
                type=block.get("block_type", "正文"),
                content=block.get("content", ""),
                confidence=int(block.get("confidence", 0)),
                status=block.get("status", "pending"),
            )
            for block in blocks
        ],
    )


async def resolve_block(
    session: AsyncSession,
    *,
    user: CurrentUser,
    task_id: str,
    payload: ResolveIn,
) -> ResolveOut:
    task = await session.get(AnnotationTask, task_id)
    if task is None or task.tenant_id != user.tenant_id:
        raise NotFound("校对任务不存在")
    block = await repo.get_block(task_id, payload.block_id)
    if block is None:
        raise NotFound("校对块不存在")
    content = payload.content.strip() if payload.content else None
    if payload.action == "resolve" and content is not None and not content:
        raise AppError("校对内容不能为空")

    await repo.update_block(
        payload.block_id,
        status="flagged" if payload.action == "flag" else "resolved",
        content=content,
    )
    blocks = await repo.blocks_for(task_id)
    resolved = sum(1 for item in blocks if item.get("status") != "pending")
    task.cursor = resolved
    task.status = "resolved" if resolved >= len(blocks) else "in_review"
    if task.status == "resolved":
        task.finished_at = utcnow()
    task.updated_at = utcnow()
    task.resolution = {
        "lastBlockId": payload.block_id,
        "lastAction": payload.action,
        "resolved": resolved,
        "total": len(blocks),
    }
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="annotation.resolve",
        resource_type="annotation_task",
        resource_id=task_id,
        before={"blockStatus": block.get("status")},
        after={"blockStatus": "flagged" if payload.action == "flag" else "resolved"},
    )
    await session.commit()
    return ResolveOut(resolved=resolved, total=len(blocks))
