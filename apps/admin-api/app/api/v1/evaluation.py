from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.evaluation import service
from app.modules.evaluation.schemas import (
    EvalDatasetOut,
    EvalReportOut,
    EvalRunIn,
    EvalRunOut,
)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/datasets", response_model=Envelope[list[EvalDatasetOut]])
async def list_datasets(
    user: CurrentUser = Depends(require("evaluation.view")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.list_datasets(session, user.tenant_id))


@router.get("/runs", response_model=Envelope[PageResult[EvalRunOut]])
async def list_runs(
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("evaluation.view")),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.list_runs(
        session, tenant_id=user.tenant_id, page=params.page, page_size=params.page_size
    )
    return ok(page_result(items, total, params))


@router.get("/runs/{run_id}", response_model=Envelope[EvalReportOut])
async def get_report(
    run_id: str,
    user: CurrentUser = Depends(require("evaluation.view")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.get_report(session, tenant_id=user.tenant_id, run_id=run_id))


@router.post("/runs", response_model=Envelope[EvalRunOut])
async def create_run(
    payload: EvalRunIn,
    user: CurrentUser = Depends(require("evaluation.run")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.create_run(session, user=user, payload=payload))
