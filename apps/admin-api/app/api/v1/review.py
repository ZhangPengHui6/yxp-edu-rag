from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.review import service
from app.modules.review.schemas import DecisionIn, DecisionOut, ReviewTaskOut

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/tasks", response_model=Envelope[PageResult[ReviewTaskOut]])
async def list_tasks(
    kind: str | None = Query(default=None),
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("review.view")),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.list_tasks(
        session,
        tenant_id=user.tenant_id,
        page=params.page,
        page_size=params.page_size,
        kind=kind,
    )
    return ok(page_result(items, total, params))


@router.post("/tasks/{task_id}/decision", response_model=Envelope[DecisionOut])
async def decide(
    task_id: str,
    payload: DecisionIn,
    user: CurrentUser = Depends(require("review.decide")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.decide(session, user=user, task_id=task_id, payload=payload))
