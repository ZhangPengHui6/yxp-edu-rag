from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.errors import Forbidden
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.minio import LocalStorage, get_storage
from app.infra.pg import get_session
from app.modules.ingestion import service
from app.modules.ingestion.schemas import (
    CreateJobIn,
    IngestionJobOut,
    PresignIn,
    PresignOut,
)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.get("/jobs", response_model=Envelope[PageResult[IngestionJobOut]])
async def list_jobs(
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("ingestion.view")),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.list_jobs(
        session,
        tenant_id=user.tenant_id,
        page=params.page,
        page_size=params.page_size,
        status=status,
        keyword=keyword,
    )
    return ok(page_result(items, total, params))


@router.get("/jobs/{job_id}", response_model=Envelope[IngestionJobOut])
async def get_job(
    job_id: str,
    user: CurrentUser = Depends(require("ingestion.view")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.get_job(session, tenant_id=user.tenant_id, job_id=job_id))


@router.post("/uploads", response_model=Envelope[PresignOut])
async def presign_upload(
    payload: PresignIn,
    _: CurrentUser = Depends(require("ingestion.create")),
):
    return ok(await service.create_presign(payload.filename))


@router.post("/jobs", response_model=Envelope[IngestionJobOut])
async def create_job(
    payload: CreateJobIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(require("ingestion.create")),
    session: AsyncSession = Depends(get_session),
):
    job = await service.create_job(
        session, user=user, payload=payload, idempotency_key=idempotency_key
    )
    return ok(job)


@router.post("/jobs/{job_id}/retry", response_model=Envelope[IngestionJobOut])
async def retry_job(
    job_id: str,
    user: CurrentUser = Depends(require("ingestion.retry")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.retry_job(session, user=user, job_id=job_id))


@router.post("/jobs/{job_id}/cancel", response_model=Envelope[IngestionJobOut])
async def cancel_job(
    job_id: str,
    user: CurrentUser = Depends(require("ingestion.cancel")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await service.cancel_job(session, user=user, job_id=job_id))


@router.put("/_dev/uploads/{key:path}", include_in_schema=False)
async def dev_upload(
    key: str,
    request: Request,
    expires: int = Query(...),
    sig: str = Query(...),
):
    """Local object-storage endpoint used by the presigned URL in development."""
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise Forbidden("当前存储后端不是本地存储")
    if not key.startswith("uploads/") or not storage.verify(key, expires, sig):
        raise Forbidden("上传链接无效或已过期")
    data = await request.body()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise Forbidden("文件超过大小限制")
    await storage.put_bytes(key, data, request.headers.get("content-type", ""))
    return Response(status_code=204)
