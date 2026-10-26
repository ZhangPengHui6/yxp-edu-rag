"""Review queue and decisions."""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import InvalidState, NotFound
from app.models import ReviewTask
from app.modules.audit import service as audit
from app.modules.question import repository as question_repo
from app.modules.review.schemas import (
    DecisionIn,
    DecisionOut,
    ReviewPayloadOut,
    ReviewTaskOut,
)
from app.shared.time import fmt_dt, utcnow

_ACTIVE = {"pending", "in_review"}


async def list_tasks(
    session: AsyncSession,
    *,
    tenant_id: str,
    page: int,
    page_size: int,
    kind: str | None,
) -> tuple[list[ReviewTaskOut], int]:
    filters = [ReviewTask.tenant_id == tenant_id, ReviewTask.status.in_(_ACTIVE)]
    if kind and kind != "all":
        filters.append(ReviewTask.kind == kind)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(ReviewTask).where(*filters)
            )
        ).scalar_one()
    )
    priority_order = case((ReviewTask.priority == "high", 0), else_=1)
    tasks = (
        (
            await session.execute(
                select(ReviewTask)
                .where(*filters)
                .order_by(priority_order, ReviewTask.created_at.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        ReviewTaskOut(
            id=task.task_id,
            kind=task.kind,  # type: ignore[arg-type]
            title=task.title,
            submitter=task.submitter_name,
            submitted_at=fmt_dt(task.created_at),
            priority=task.priority,  # type: ignore[arg-type]
            payload=ReviewPayloadOut(**task.payload),
        )
        for task in tasks
    ]
    return items, total


async def decide(
    session: AsyncSession,
    *,
    user: CurrentUser,
    task_id: str,
    payload: DecisionIn,
) -> DecisionOut:
    task = await session.get(ReviewTask, task_id)
    if task is None or task.tenant_id != user.tenant_id:
        raise NotFound("审核任务不存在")
    if task.status not in _ACTIVE:
        raise InvalidState("审核任务已处理", {"status": task.status})

    task.status = "approved" if payload.decision == "approve" else "rejected"
    task.decision = payload.decision
    task.comment = payload.comment or ""
    task.assignee_id = user.id
    task.finished_at = utcnow()
    task.updated_at = utcnow()

    if task.asset_type == "question":
        await question_repo.set_review_status(
            task.asset_id, "approved" if payload.decision == "approve" else "draft"
        )

    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="review.decide",
        resource_type=task.asset_type,
        resource_id=task.asset_id,
        before={"status": "pending", "kind": task.kind},
        after={"decision": payload.decision, "comment": payload.comment or ""},
    )
    audit.emit(
        session,
        tenant_id=user.tenant_id,
        event_type="review.decided",
        aggregate_type=task.asset_type,
        aggregate_id=task.asset_id,
        payload={"decision": payload.decision, "taskId": task_id},
    )
    await session.commit()
    return DecisionOut(id=task_id, decision=payload.decision, comment=task.comment)
