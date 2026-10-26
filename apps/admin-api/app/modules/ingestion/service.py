"""Ingestion use cases: presign, create, retry, cancel, read models."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import NotFound
from app.infra.minio import get_storage
from app.models import IngestionJob, IngestionJobStep
from app.modules.audit import service as audit
from app.modules.ingestion.rules import STEP_DEFS, STEP_LABELS, ui_status
from app.modules.ingestion.schemas import (
    CreateJobIn,
    IngestionJobOut,
    JobStepOut,
    PresignOut,
)
from app.shared.ids import new_id
from app.shared.time import fmt_dt, utcnow


async def _steps_for(session: AsyncSession, job_id: str) -> list[IngestionJobStep]:
    return list(
        (
            await session.execute(
                select(IngestionJobStep)
                .where(IngestionJobStep.job_id == job_id)
                .order_by(IngestionJobStep.position.asc())
            )
        )
        .scalars()
        .all()
    )


def _to_out(job: IngestionJob, steps: list[IngestionJobStep]) -> IngestionJobOut:
    return IngestionJobOut(
        id=job.job_id,
        name=job.source_name,
        source_type=job.source_type,  # type: ignore[arg-type]
        subject=job.subject,
        stage=job.stage,  # type: ignore[arg-type]
        pages=job.pages,
        size_mb=round(job.size_mb, 1),
        progress=job.progress,
        status=job.status,  # type: ignore[arg-type]
        steps=[
            JobStepOut(
                key=step.step,
                label=step.label or STEP_LABELS.get(step.step, step.step),
                status=ui_status(step.status),  # type: ignore[arg-type]
                detail=step.detail or None,
            )
            for step in steps
        ],
        created_at=fmt_dt(job.created_at),
        error=job.error_message or None,
    )


async def list_jobs(
    session: AsyncSession,
    *,
    tenant_id: str,
    page: int,
    page_size: int,
    status: str | None,
    keyword: str | None,
) -> tuple[list[IngestionJobOut], int]:
    filters = [IngestionJob.tenant_id == tenant_id, IngestionJob.deleted_at.is_(None)]
    if status and status != "all":
        filters.append(IngestionJob.status == status)
    if keyword:
        filters.append(IngestionJob.source_name.contains(keyword))
    total = int(
        (await session.execute(select(func.count()).select_from(IngestionJob).where(*filters)))
        .scalar_one()
    )
    jobs = (
        (
            await session.execute(
                select(IngestionJob)
                .where(*filters)
                .order_by(IngestionJob.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    result: list[IngestionJobOut] = []
    for job in jobs:
        result.append(_to_out(job, await _steps_for(session, job.job_id)))
    return result, total


async def get_job(session: AsyncSession, *, tenant_id: str, job_id: str) -> IngestionJobOut:
    job = await session.get(IngestionJob, job_id)
    if job is None or job.tenant_id != tenant_id or job.deleted_at is not None:
        raise NotFound("入库任务不存在")
    return _to_out(job, await _steps_for(session, job_id))


async def create_presign(filename: str) -> PresignOut:
    result = await get_storage().presign_put(filename)
    return PresignOut(**result)


async def create_job(
    session: AsyncSession,
    *,
    user: CurrentUser,
    payload: CreateJobIn,
    idempotency_key: str | None,
) -> IngestionJobOut:
    if idempotency_key:
        existing = (
            await session.execute(
                select(IngestionJob).where(
                    IngestionJob.tenant_id == user.tenant_id,
                    IngestionJob.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _to_out(existing, await _steps_for(session, existing.job_id))

    job = IngestionJob(
        job_id=new_id("job"),
        tenant_id=user.tenant_id,
        job_type="textbook",
        source_type=payload.source_type,
        source_name=payload.filename,
        source_uri=payload.object_key,
        subject=payload.subject,
        stage=payload.stage,
        pages=payload.pages,
        size_mb=payload.size_mb,
        progress=0,
        status="pending",
        current_step="upload",
        stats={"mode": "poc-pipeline"},
        idempotency_key=idempotency_key,
        created_by=user.id,
    )
    session.add(job)
    for position, step in enumerate(STEP_DEFS):
        session.add(
            IngestionJobStep(
                step_id=new_id("stp"),
                job_id=job.job_id,
                tenant_id=user.tenant_id,
                step=step["key"],
                position=position,
                label=step["label"],
                status="pending",
            )
        )
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="ingestion.create",
        resource_type="ingestion_job",
        resource_id=job.job_id,
        after={"name": job.source_name, "sourceType": job.source_type},
    )
    await session.commit()

    import contextvars

    from app.infra.queue import dispatch_ingestion_with_context

    dispatch_ingestion_with_context(job.job_id, contextvars.copy_context())
    return _to_out(job, await _steps_for(session, job.job_id))


async def retry_job(
    session: AsyncSession, *, user: CurrentUser, job_id: str
) -> IngestionJobOut:
    job = await session.get(IngestionJob, job_id)
    if job is None or job.tenant_id != user.tenant_id or job.deleted_at is not None:
        raise NotFound("入库任务不存在")
    if job.status not in {"failed", "canceled"}:
        from app.core.errors import InvalidState

        raise InvalidState("只有失败或已终止的任务可以重试", {"status": job.status})
    await session.execute(delete(IngestionJobStep).where(IngestionJobStep.job_id == job_id))
    for position, step in enumerate(STEP_DEFS):
        session.add(
            IngestionJobStep(
                step_id=new_id("stp"),
                job_id=job_id,
                tenant_id=job.tenant_id,
                step=step["key"],
                position=position,
                label=step["label"],
                status="pending",
                attempt=1,
            )
        )
    job.status = "pending"
    job.progress = 0
    job.current_step = "upload"
    job.error_code = ""
    job.error_message = ""
    job.started_at = None
    job.finished_at = None
    job.updated_at = utcnow()
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="ingestion.retry",
        resource_type="ingestion_job",
        resource_id=job_id,
    )
    await session.commit()

    from app.infra.queue import dispatch_ingestion

    dispatch_ingestion(job_id)
    return _to_out(job, await _steps_for(session, job_id))


async def cancel_job(
    session: AsyncSession, *, user: CurrentUser, job_id: str
) -> IngestionJobOut:
    job = await session.get(IngestionJob, job_id)
    if job is None or job.tenant_id != user.tenant_id or job.deleted_at is not None:
        raise NotFound("入库任务不存在")
    if job.status not in {"pending", "running"}:
        from app.core.errors import InvalidState

        raise InvalidState("当前任务不可终止", {"status": job.status})
    job.status = "canceled"
    job.error_message = "用户终止"
    job.finished_at = utcnow()
    job.updated_at = utcnow()
    steps = await _steps_for(session, job_id)
    for step in steps:
        if step.status == "running":
            step.status = "pending"
            step.detail = "任务已终止"
            step.finished_at = utcnow()
    audit.record(
        session,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_name=user.name,
        action="ingestion.cancel",
        resource_type="ingestion_job",
        resource_id=job_id,
    )
    await session.commit()
    return _to_out(job, await _steps_for(session, job_id))
