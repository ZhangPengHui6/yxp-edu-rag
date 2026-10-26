from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.annotation import service
from app.modules.annotation.schemas import (
    AnnotationTaskOut,
    AnnotationTaskRowOut,
    ResolveIn,
    ResolveOut,
)

router = APIRouter(prefix="/annotation", tags=["annotation"])


@router.get("/tasks", response_model=Envelope[PageResult[AnnotationTaskRowOut]])
async def list_tasks(
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("annotation.view")),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.list_tasks(
        session, tenant_id=user.tenant_id, page=params.page, page_size=params.page_size
    )
    return ok(page_result(items, total, params))


@router.get("/tasks/{task_id}", response_model=Envelope[AnnotationTaskOut])
async def get_task(
    task_id: str,
    user: CurrentUser = Depends(require("annotation.view")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.get_task(session, tenant_id=user.tenant_id, task_id=task_id))


@router.post("/tasks/{task_id}/resolve", response_model=Envelope[ResolveOut])
async def resolve(
    task_id: str,
    payload: ResolveIn,
    user: CurrentUser = Depends(require("annotation.resolve")),
    session: AsyncSession = Depends(get_session),
):
    return ok(
        await service.resolve_block(
            session, user=user, task_id=task_id, payload=payload
        )
    )
