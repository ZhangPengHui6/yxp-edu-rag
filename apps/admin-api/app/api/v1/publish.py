from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.publish import service
from app.modules.publish.schemas import PublishCreateIn, PublishOrderOut

router = APIRouter(prefix="/publish", tags=["publish"])


@router.get("/orders", response_model=Envelope[PageResult[PublishOrderOut]])
async def list_orders(
    status: str | None = Query(default=None),
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("publish.view")),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.list_orders(
        session,
        tenant_id=user.tenant_id,
        page=params.page,
        page_size=params.page_size,
        status=status,
    )
    return ok(page_result(items, total, params))


@router.get("/orders/{publish_id}", response_model=Envelope[PublishOrderOut])
async def get_order(
    publish_id: str,
    user: CurrentUser = Depends(require("publish.view")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.get_order(session, tenant_id=user.tenant_id, publish_id=publish_id))


@router.post("/orders", response_model=Envelope[PublishOrderOut])
async def create_order(
    payload: PublishCreateIn,
    user: CurrentUser = Depends(require("publish.create")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.create_order(session, user=user, payload=payload))


@router.post("/orders/{publish_id}/stage", response_model=Envelope[PublishOrderOut])
async def stage_order(
    publish_id: str,
    user: CurrentUser = Depends(require("publish.commit")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.stage_order(session, user=user, publish_id=publish_id))


@router.post("/orders/{publish_id}/commit", response_model=Envelope[PublishOrderOut])
async def commit_order(
    publish_id: str,
    user: CurrentUser = Depends(require("publish.commit")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.commit_order(session, user=user, publish_id=publish_id))


@router.post("/orders/{publish_id}/rollback", response_model=Envelope[PublishOrderOut])
async def rollback_order(
    publish_id: str,
    user: CurrentUser = Depends(require("publish.rollback")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.rollback_order(session, user=user, publish_id=publish_id))
