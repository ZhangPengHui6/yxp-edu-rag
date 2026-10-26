from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require
from app.core.envelope import Envelope, ok
from app.core.pagination import PageParams, PageResult, page_params, page_result
from app.infra.pg import get_session
from app.modules.audit import service as audit_service
from app.modules.audit.schemas import AuditLogOut
from app.modules.iam import service as iam
from app.modules.iam.schemas import RoleRowOut, TenantOut, UserOut

router = APIRouter(prefix="/iam", tags=["iam"])


@router.get("/users", response_model=Envelope[PageResult[UserOut]])
async def list_users(
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("iam.user")),
    session: AsyncSession = Depends(get_session),
):
    scope = None if user.role_code == "super_admin" else user.tenant_id
    items, total = await iam.list_users(
        session, tenant_id=scope, page=params.page, page_size=params.page_size
    )
    return ok(page_result(items, total, params))


@router.get("/roles", response_model=Envelope[list[RoleRowOut]])
async def list_roles(
    _: CurrentUser = Depends(require("iam.role")),
    session: AsyncSession = Depends(get_session),
):
    return ok(await iam.list_roles(session))


@router.get("/dicts", response_model=Envelope[dict[str, list[str]]])
async def dictionaries(_: CurrentUser = Depends(require("iam.dict"))):
    return ok(iam.dictionaries())


@router.get("/tenants", response_model=Envelope[PageResult[TenantOut]])
async def list_tenants(
    params: PageParams = Depends(page_params),
    _: CurrentUser = Depends(require("iam.tenant")),
    session: AsyncSession = Depends(get_session),
):
    items, total = await iam.list_tenants(
        session, page=params.page, page_size=params.page_size
    )
    return ok(page_result(items, total, params))


@router.get("/audit", response_model=Envelope[PageResult[AuditLogOut]])
async def audit_logs(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(require("iam.audit")),
    session: AsyncSession = Depends(get_session),
):
    scope = None if user.role_code == "super_admin" else user.tenant_id
    items, total = await audit_service.list_logs(
        session,
        tenant_id=scope,
        page=params.page,
        page_size=params.page_size,
        actor=actor,
        action=action,
    )
    return ok(page_result(items, total, params))
