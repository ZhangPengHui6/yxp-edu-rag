from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.infra.pg import get_session
from app.modules.dashboard import service
from app.modules.dashboard.schemas import DashboardSummaryOut, TodoItemOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=Envelope[DashboardSummaryOut])
async def summary(
    user: CurrentUser = Depends(require("dashboard.view")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.summary(session, user=user))


@router.get("/todos", response_model=Envelope[list[TodoItemOut]])
async def todos(
    user: CurrentUser = Depends(require("dashboard.view")),
    session: AsyncSession = Depends(get_session),
):
    data = await service.summary(session, user=user)
    return ok(data.todos)
