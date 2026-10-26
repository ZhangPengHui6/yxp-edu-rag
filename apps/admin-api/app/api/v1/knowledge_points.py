from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.infra.pg import get_session
from app.modules.knowledge import service
from app.modules.knowledge.schemas import (
    CoverageReportOut,
    KnowledgeDetailOut,
    KnowledgeNodeOut,
    MergeIn,
    MergeResultOut,
)

router = APIRouter(prefix="/knowledge-points", tags=["knowledge-points"])


@router.get("/tree", response_model=Envelope[list[KnowledgeNodeOut]])
async def tree(
    user: CurrentUser = Depends(require("knowledge.view")),
):
    return ok(await service.get_tree(user.tenant_id))


@router.get("/coverage", response_model=Envelope[CoverageReportOut])
async def coverage(
    user: CurrentUser = Depends(require("knowledge.coverage")),
):
    return ok(await service.get_coverage(user.tenant_id))


@router.get("/{node_id}", response_model=Envelope[KnowledgeDetailOut])
async def detail(
    node_id: str,
    user: CurrentUser = Depends(require("knowledge.view")),
):
    return ok(await service.get_detail(user.tenant_id, node_id))


@router.post("/{node_id}/merge", response_model=Envelope[MergeResultOut])
async def merge(
    node_id: str,
    payload: MergeIn,
    user: CurrentUser = Depends(require("knowledge.merge")),
    session: AsyncSession = Depends(get_session),
):
    return ok(
        await service.merge_nodes(
            session, user=user, source_id=node_id, target_id=payload.target_id
        )
    )
